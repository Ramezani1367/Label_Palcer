"""Web front for the smart label placer."""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import pymupdf
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

import engine

def resource_root() -> Path:
    """Prefer folders beside the exe so the modular build can be edited in place."""
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
        if (beside / "static").is_dir() and (beside / "templates").is_dir():
            return beside
        return Path(getattr(sys, "_MEIPASS", beside))
    return Path(__file__).resolve().parent


def data_root() -> Path:
    """Writable job folder. Next to the exe when frozen, so it survives relaunch."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent / "LabelPlacer-data"
    else:
        base = Path(__file__).resolve().parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


ROOT = resource_root()
JOBS = data_root() / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    static_folder=str(ROOT / "static"),
    template_folder=str(ROOT / "templates"),
)
app.config["MAX_CONTENT_LENGTH"] = 280 * 1024 * 1024
app.json.ensure_ascii = False

LOCKS: dict[str, threading.Lock] = {}
RASTER_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
PSD_EXT = {".psd", ".psb"}
PDF_EXT = {".pdf"}
ALLOWED = RASTER_EXT | PSD_EXT | PDF_EXT

PUBLIC_FIELDS = (
    "id", "name", "kind", "width_mm", "height_mm", "width_px", "height_px",
    "dpi", "dpi_assumed", "bytes", "trimbox", "detected_trim", "warnings",
    "page_count", "page_index", "copies",
    "batch_id", "batch_name", "batch_index",
)


def lock_for(job_id: str) -> threading.Lock:
    return LOCKS.setdefault(job_id, threading.Lock())


def job_dir(job_id: str) -> Path:
    path = JOBS / job_id
    if not path.exists():
        raise engine.LayoutError("این کار پیدا نشد. صفحه را تازه کنید.")
    return path


def load_job(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job: dict) -> None:
    path = JOBS / job["id"] / "job.json"
    path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")


def public_file(info: dict) -> dict:
    return {key: info.get(key) for key in PUBLIC_FIELDS}


def new_job() -> dict:
    job_id = uuid.uuid4().hex[:12]
    folder = JOBS / job_id
    (folder / "files").mkdir(parents=True)
    (folder / "thumbs").mkdir()
    job = {"id": job_id, "created": time.time(), "files": []}
    save_job(job)
    return job


def _thumb(image: Image.Image, dest: Path) -> None:
    thumb = image.copy()
    thumb.thumbnail((560, 560))
    if thumb.mode not in ("RGB", "RGBA"):
        thumb = thumb.convert("RGB")
    thumb.save(dest, format="PNG")


def inspect_raster(path: Path, thumb_path: Path) -> dict:
    warnings = []
    image = Image.open(path)
    try:
        orientation = image.getexif().get(274, 1)
    except Exception:
        orientation = 1
    working = path
    if orientation not in (1, None, 0):
        image = ImageOps.exif_transpose(image)
        working = path.with_name(path.stem + "_norm.png")
        image.save(working)
        warnings.append("جهت EXIF تصویر اصلاح شد.")
    dpi = None
    info = image.info.get("dpi")
    if info and info[0]:
        dpi = float(info[0])
    width_px, height_px = image.size
    width_mm, height_mm, use_dpi, assumed = engine.inspect_raster_size(width_px, height_px, dpi, dpi is None)
    if assumed:
        shown = int(dpi) if dpi else "نامشخص"
        warnings.append(f"رزولوشن فایل {shown} است؛ برای چاپ ۳۰۰ dpi فرض شد.")
    detected = None
    try:
        detected = engine.detect_crop_margins(image.convert("RGB"), use_dpi)
    except Exception:
        detected = None
    if detected and detected.get("confidence", 0) >= 0.75:
        warnings.append(
            "کراپ‌مارک داخل تصویر تشخیص داده شد و هنگام چیدمان بریده می‌شود."
        )
    _thumb(image, thumb_path)
    return {
        "kind": "image",
        "path": str(working),
        "width_mm": round(width_mm, 3),
        "height_mm": round(height_mm, 3),
        "width_px": width_px,
        "height_px": height_px,
        "dpi": round(use_dpi, 2),
        "dpi_assumed": assumed,
        "trimbox": None,
        "detected_trim": detected,
        "warnings": warnings,
        "page_count": 1,
        "page_index": 0,
    }


def inspect_psd(path: Path, thumb_path: Path) -> dict:
    from psd_tools import PSDImage

    warnings = []
    psd = PSDImage.open(path)
    dpi = engine.read_psd_dpi(psd)
    width_px, height_px = psd.width, psd.height
    width_mm, height_mm, use_dpi, assumed = engine.inspect_raster_size(
        width_px, height_px, dpi, dpi is None
    )
    if assumed:
        warnings.append("رزولوشن PSD پیدا نشد؛ ۳۰۰ dpi فرض شد.")
    thumb = None
    try:
        has_thumb = psd.has_thumbnail() if callable(getattr(psd, "has_thumbnail", None)) else False
        if has_thumb:
            thumb = psd.thumbnail()
    except Exception:
        thumb = None
    if thumb is None:
        thumb = psd.composite()
    detected = None
    try:
        detected = engine.detect_crop_margins(thumb.convert("RGB"), use_dpi)
    except Exception:
        detected = None
    _thumb(thumb, thumb_path)
    return {
        "kind": "psd",
        "path": str(path),
        "width_mm": round(width_mm, 3),
        "height_mm": round(height_mm, 3),
        "width_px": width_px,
        "height_px": height_px,
        "dpi": round(use_dpi, 2),
        "dpi_assumed": assumed,
        "trimbox": None,
        "detected_trim": detected,
        "warnings": warnings,
        "page_count": 1,
        "page_index": 0,
    }


def inspect_pdf(path: Path, thumb_path: Path, page_index: int = 0) -> dict:
    doc = pymupdf.open(path)
    try:
        if page_index >= doc.page_count:
            page_index = 0
        page = doc[page_index]
        info = engine.inspect_pdf_page(page)
        # Full page, so the preview can clip TrimBox itself and show the marks disappearing.
        zoom = 560 / max(page.rect.width, page.rect.height, 1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pix.save(str(thumb_path))
        warnings = list(info["warnings"])
        if info["trimbox"]:
            warnings.append("TrimBox پیدا شد؛ کراپ‌مارک بیرون آن در چیدمان حذف می‌شود.")
        if doc.page_count > 1 and page_index == 0:
            warnings.append(f"این PDF {doc.page_count} صفحه دارد. پیش‌فرض، صفحه اول است.")
        return {
            "kind": "pdf",
            "path": str(path),
            "width_mm": round(info["width_mm"], 3),
            "height_mm": round(info["height_mm"], 3),
            "width_px": None,
            "height_px": None,
            "dpi": None,
            "dpi_assumed": False,
            "trimbox": info["trimbox"],
            "detected_trim": None,
            "warnings": warnings,
            "page_count": doc.page_count,
            "page_index": page_index,
        }
    finally:
        doc.close()


def add_path(job: dict, source: Path, display_name: str, all_pages: bool = False,
            batch_id: str = "", batch_name: str = "", batch_index: int = 1) -> list[dict]:
    ext = source.suffix.lower()
    folder = JOBS / job["id"]
    added = []
    targets = [(source, 0, display_name)]
    if ext in PDF_EXT and all_pages:
        doc = pymupdf.open(source)
        count = doc.page_count
        doc.close()
        targets = []
        for index in range(count):
            stem = Path(display_name).stem
            suffix = Path(display_name).suffix
            targets.append((source, index, f"{stem}-p{index + 1}{suffix}"))
    for src, page_index, name in targets:
        file_id = uuid.uuid4().hex[:8]
        stored = folder / "files" / f"{file_id}{ext}"
        if not stored.exists():
            shutil.copy(src, stored)
        thumb = folder / "thumbs" / f"{file_id}.png"
        if ext in PDF_EXT:
            meta = inspect_pdf(stored, thumb, page_index)
        elif ext in PSD_EXT:
            meta = inspect_psd(stored, thumb)
        else:
            meta = inspect_raster(stored, thumb)
        meta.update({
            "id": file_id,
            "name": name,
            "bytes": stored.stat().st_size,
            "copies": 1,
            "batch_id": batch_id or "default",
            "batch_name": batch_name or "لیبل‌ها",
            "batch_index": int(batch_index or 1),
        })
        job["files"].append(meta)
        added.append(meta)
    return added


def apply_runtime_size(files: list[dict], settings: dict) -> list[dict]:
    override = settings.get("dpi_override")
    if not override:
        return files
    try:
        dpi = float(override)
    except (TypeError, ValueError):
        return files
    if dpi < 36 or dpi > 2400:
        raise engine.LayoutError("DPI باید بین ۳۶ و ۲۴۰۰ باشد.")
    adjusted = []
    for info in files:
        if info.get("kind") == "pdf" or not info.get("width_px"):
            adjusted.append(info)
            continue
        clone = dict(info)
        clone["width_mm"] = round(info["width_px"] / dpi * 25.4, 3)
        clone["height_mm"] = round(info["height_px"] / dpi * 25.4, 3)
        clone["dpi"] = dpi
        clone["dpi_assumed"] = False
        adjusted.append(clone)
    return adjusted


def ordered_files(job: dict, order: list[dict] | None, settings: dict) -> list[dict]:
    by_id = {item["id"]: item for item in job["files"]}
    chosen = []
    if order:
        for item in order:
            src = by_id.get(item.get("id"))
            if not src:
                continue
            clone = dict(src)
            clone["copies"] = max(0, int(item.get("copies") or 0))
            chosen.append(clone)
    else:
        chosen = [dict(item) for item in job["files"]]
    return apply_runtime_size(chosen, settings)


def raster_for_placement(info: dict, trim: dict, cache: Path) -> str:
    if info["kind"] == "pdf":
        return info["path"]
    if info["kind"] == "psd":
        key = f"{info['id']}_psd"
        dest = cache / f"{key}.png"
        if not dest.exists():
            from psd_tools import PSDImage
            image = PSDImage.open(info["path"]).composite()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.save(dest)
        source = dest
        dpi = float(info.get("dpi") or 300)
    else:
        source = info["path"]
        dpi = float(info.get("dpi") or 300)
    if all(float(trim.get(k) or 0) < 0.05 for k in ("left", "top", "right", "bottom")):
        return source
    tag = "_".join(f"{float(trim.get(k) or 0):.2f}" for k in ("left", "top", "right", "bottom"))
    dest = cache / f"{info['id']}_{tag}.png"
    if dest.exists():
        return str(dest)
    image = Image.open(source)
    px = dpi / 25.4
    left = int(round(float(trim["left"]) * px))
    top = int(round(float(trim["top"]) * px))
    right = int(round(float(trim["right"]) * px))
    bottom = int(round(float(trim["bottom"]) * px))
    width, height = image.size
    box = (left, top, max(left + 1, width - right), max(top + 1, height - bottom))
    cropped = image.crop(box)
    if cropped.mode == "CMYK":
        dest = dest.with_suffix(".tif")
        cropped.save(dest, format="TIFF", compression="tiff_lzw")
    else:
        if cropped.mode not in ("RGB", "RGBA", "L"):
            cropped = cropped.convert("RGB")
        cropped.save(dest, format="PNG")
    return str(dest)


def pdf_clip(info: dict, settings: dict):
    if info.get("kind") != "pdf" or not settings.get("use_trimbox", True):
        return None
    if not info.get("trimbox"):
        return None
    doc = pymupdf.open(info["path"])
    try:
        page = doc[info.get("page_index") or 0]
        if page.rotation:
            return None
        trim = page.trimbox
        if trim and (page.rect.width - trim.width > 1.5 or page.rect.height - trim.height > 1.5):
            return pymupdf.Rect(trim)
        return None
    finally:
        doc.close()


def placement_lookup(files: list[dict], settings: dict, cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    lookup = {}
    for info in files:
        trim, _source = engine.resolve_trim(info, settings)
        lookup[info["id"]] = {
            "kind": "pdf" if info["kind"] == "pdf" else "image",
            "path": info["path"],
            "raster_path": None if info["kind"] == "pdf" else raster_for_placement(info, trim, cache),
            "pdf_clip": pdf_clip(info, settings),
            "page_index": info.get("page_index") or 0,
            "name": info.get("name"),
        }
    return lookup


def error(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


@app.errorhandler(413)
def too_large(_exc):
    return error("حجم فایل از حد مجاز بیشتر است.", 413)


@app.errorhandler(engine.LayoutError)
def layout_error(exc):
    return error(exc.message)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/catalog")
def catalog():
    return jsonify({
        "papers": [
            {"id": p["id"], "name": p["name"], "w": p["w"], "h": p["h"], "group": p["group"], "common": p["common"]}
            for p in engine.PAPERS
        ],
        "presets": engine.PRESETS,
        "defaults": engine.DEFAULT_SETTINGS,
    })


@app.post("/api/jobs")
def create_job():
    return jsonify({"ok": True, "id": new_job()["id"], "files": []})


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    try:
        job = load_job(job_id)
    except engine.LayoutError as exc:
        return error(exc.message, 404)
    return jsonify({"ok": True, "id": job_id, "files": [public_file(item) for item in job["files"]]})


@app.post("/api/jobs/<job_id>/files")
def upload_files(job_id):
    try:
        with lock_for(job_id):
            job = load_job(job_id)
            uploads = request.files.getlist("files")
            if not uploads:
                return error("فایلی انتخاب نشد.")
            all_pages = request.form.get("all_pages") == "1"
            batch_id = (request.form.get("batch_id") or "").strip()[:40] or uuid.uuid4().hex[:8]
            batch_name = (request.form.get("batch_name") or "").strip()[:80] or "پوشه"
            try:
                batch_index = max(1, int(request.form.get("batch_index") or 1))
            except ValueError:
                batch_index = 1
            added = []
            rejected = []
            for upload in uploads:
                name = upload.filename or "label"
                ext = Path(name).suffix.lower()
                if ext not in ALLOWED:
                    rejected.append(f"{Path(name).name}: این فرمت پشتیبانی نمی‌شود. PDF، PSD یا تصویر بدهید.")
                    continue
                safe = secure_filename(Path(name).name) or f"label{ext}"
                temp = JOBS / job_id / "files" / f"_up_{uuid.uuid4().hex[:6]}_{safe}"
                upload.save(temp)
                try:
                    added.extend(add_path(
                        job, temp, Path(name).name, all_pages=all_pages,
                        batch_id=batch_id, batch_name=batch_name, batch_index=batch_index,
                    ))
                finally:
                    if temp.exists():
                        temp.unlink()
            added.sort(key=lambda item: engine.natural_key(item.get("name") or ""))
            added_ids = {item["id"] for item in added}
            job["files"] = [item for item in job["files"] if item["id"] not in added_ids] + added
            save_job(job)
            return jsonify({
                "ok": True,
                "files": [public_file(item) for item in job["files"]],
                "added": [public_file(item) for item in added],
                "rejected": rejected,
            })
    except engine.LayoutError as exc:
        return error(exc.message, 404)
    except Exception as exc:
        return error(f"خواندن فایل ناموفق بود: {exc}")


@app.delete("/api/jobs/<job_id>/files/<file_id>")
def delete_file(job_id, file_id):
    try:
        with lock_for(job_id):
            job = load_job(job_id)
            job["files"] = [item for item in job["files"] if item["id"] != file_id]
            save_job(job)
            return jsonify({"ok": True, "files": [public_file(item) for item in job["files"]]})
    except engine.LayoutError as exc:
        return error(exc.message, 404)


@app.get("/api/jobs/<job_id>/thumb/<file_id>")
def thumb(job_id, file_id):
    path = JOBS / job_id / "thumbs" / f"{file_id}.png"
    if not path.exists():
        return error("پیش‌نمایش پیدا نشد.", 404)
    return send_file(path, mimetype="image/png")


def _measure_file(path: Path, display_name: str, page_index: int, thumb: Path) -> dict:
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        meta = inspect_pdf(path, thumb, page_index)
    elif ext in PSD_EXT:
        meta = inspect_psd(path, thumb)
    else:
        meta = inspect_raster(path, thumb)
    meta["name"] = display_name
    return meta


@app.post("/api/measure")
def measure_uploads():
    """Read label sizes without adding them to a project."""
    uploads = request.files.getlist("files")
    if not uploads:
        return error("فایلی انتخاب نشد.")
    raw_settings = request.form.get("settings") or "{}"
    try:
        settings = json.loads(raw_settings)
    except json.JSONDecodeError:
        settings = {}
    settings = {**engine.DEFAULT_SETTINGS, **settings}
    all_pages = request.form.get("all_pages") == "1"
    try:
        reference_w = float(request.form.get("reference_w") or 0)
        reference_h = float(request.form.get("reference_h") or 0)
    except ValueError:
        reference_w = reference_h = 0
    has_reference = reference_w > 1 and reference_h > 1
    folder = JOBS / "_measure" / uuid.uuid4().hex[:10]
    folder.mkdir(parents=True)
    measured = []
    rejected = []
    try:
        for upload in uploads:
            name = Path(upload.filename or "label").name
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED:
                rejected.append(f"{name}: این فرمت پشتیبانی نمی‌شود. PDF، PSD یا تصویر بدهید.")
                continue
            stored = folder / f"{uuid.uuid4().hex[:8]}{ext}"
            upload.save(stored)
            pages = [0]
            if ext in PDF_EXT and all_pages:
                doc = pymupdf.open(stored)
                pages = list(range(doc.page_count))
                doc.close()
            for page_index in pages:
                label = name
                if len(pages) > 1:
                    stem = Path(name).stem
                    label = f"{stem}-p{page_index + 1}{Path(name).suffix}"
                meta = _measure_file(stored, label, page_index, folder / f"{stored.stem}-{page_index}.png")
                meta = apply_runtime_size([meta], settings)[0]
                width, height, _trim, source = engine.effective_label_size(meta, settings)
                row = {
                    "name": label,
                    "width_mm": round(width, 3),
                    "height_mm": round(height, 3),
                    "source": source,
                    "kind": meta.get("kind"),
                }
                if has_reference:
                    row["matches"] = engine.same_size(width, height, reference_w, reference_h)
                measured.append(row)
    except engine.LayoutError as exc:
        return error(exc.message)
    except Exception as exc:
        return error(f"سنجش اندازه ناموفق بود: {exc}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    odd = [item for item in measured if item.get("matches") is False]
    return jsonify({
        "ok": True,
        "files": measured,
        "rejected": rejected,
        "matches": has_reference and not odd and bool(measured),
        "reference_w": reference_w if has_reference else None,
        "reference_h": reference_h if has_reference else None,
    })


@app.post("/api/jobs/<job_id>/analyze")
def analyze(job_id):
    try:
        job = load_job(job_id)
    except engine.LayoutError as exc:
        return error(exc.message, 404)
    body = request.get_json(silent=True) or {}
    settings = {**engine.DEFAULT_SETTINGS, **(body.get("settings") or {})}
    try:
        files = ordered_files(job, body.get("files"), settings)
        layout = engine.compute_layout(files, settings)
        return jsonify(layout)
    except engine.LayoutError as exc:
        return error(exc.message)


@app.post("/api/jobs/<job_id>/export")
def export_pdf(job_id):
    try:
        job = load_job(job_id)
    except engine.LayoutError as exc:
        return error(exc.message, 404)
    body = request.get_json(silent=True) or {}
    settings = {**engine.DEFAULT_SETTINGS, **(body.get("settings") or {})}
    started = time.time()
    try:
        with lock_for(job_id):
            files = ordered_files(job, body.get("files"), settings)
            layout = engine.compute_layout(files, settings)
            if layout.get("finish", {}).get("resolved") is False:
                return error("اول بین برش ویزیتی و سلفون مات یکی را انتخاب کنید.")
            cache = JOBS / job_id / "cache"
            lookup = placement_lookup(files, settings, cache)
            dest = JOBS / job_id / "output.pdf"
            built = engine.build_pdf(layout, lookup, str(dest))
        report = {
            "elapsed": round(time.time() - started, 2),
            "placed": built["placed"],
            "errors": built["errors"],
            "warnings": layout["warnings"],
            "paper": layout["paper"],
            "grid": layout["grid"],
            "stats": layout["stats"],
            "reason": layout["reason"],
            "finish": layout.get("finish"),
            "slug": layout.get("slug"),
            "reference": layout["reference"],
            "crop": layout["crop"],
            "batches": layout["batches"],
            "candidates": layout["candidates"][:4],
        }
        return jsonify({"ok": True, "url": f"/api/jobs/{job_id}/output.pdf", "report": report})
    except engine.LayoutError as exc:
        return error(exc.message)
    except Exception as exc:
        return error(f"ساخت PDF ناموفق بود: {exc}")


@app.get("/api/jobs/<job_id>/output.pdf")
def download(job_id):
    path = JOBS / job_id / "output.pdf"
    if not path.exists():
        return error("اول خروجی را بسازید.", 404)
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name="label-imposition.pdf")


@app.post("/api/jobs/<job_id>/preview")
def preview(job_id):
    try:
        job = load_job(job_id)
    except engine.LayoutError as exc:
        return error(exc.message, 404)
    body = request.get_json(silent=True) or {}
    settings = {**engine.DEFAULT_SETTINGS, **(body.get("settings") or {})}
    page_index = int(body.get("page") or 0)
    try:
        with lock_for(job_id):
            files = ordered_files(job, body.get("files"), settings)
            layout = engine.compute_layout(files, settings)
            cache = JOBS / job_id / "cache"
            lookup = placement_lookup(files, settings, cache)
            dest = JOBS / job_id / "preview.pdf"
            engine.build_pdf(layout, lookup, str(dest), only_page=page_index)
            doc = pymupdf.open(dest)
            if doc.page_count == 0:
                doc.close()
                return error("پیش‌نمایش ساخته نشد.")
            page = doc[0]
            zoom = 110 / 72
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            out = JOBS / job_id / "preview.png"
            pix.save(str(out))
            doc.close()
        return send_file(out, mimetype="image/png")
    except engine.LayoutError as exc:
        return error(exc.message)
    except Exception as exc:
        return error(f"رندر پیش‌نمایش ناموفق بود: {exc}")


@app.get("/api/jobs/<job_id>/preview.png")
def preview_png(job_id):
    path = JOBS / job_id / "preview.png"
    if not path.exists():
        return error("پیش‌نمایشی نیست.", 404)
    return send_file(path, mimetype="image/png")


def _free_port(host: str, start: int = 8080) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return start


def main() -> None:
    frozen = bool(getattr(sys, "frozen", False))
    host = "127.0.0.1" if frozen else "0.0.0.0"
    port = _free_port("127.0.0.1", 8080)
    url = f"http://127.0.0.1:{port}"
    if frozen:
        print("چیدمان هوشمند لیبل")
        print(url)
        print("این پنجره را نبندید. با بستن آن، برنامه هم بسته می‌شود.")
        threading.Timer(1.1, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
