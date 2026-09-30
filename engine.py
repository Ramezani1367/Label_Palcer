"""Smart label imposition.

Reads label files, picks a paper size and orientation, gangs the labels,
and draws crop marks that never cut into a neighbouring label.
"""

from __future__ import annotations

import html
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

MM_TO_PT = 72.0 / 25.4
PT_TO_MM = 25.4 / 72.0
MAX_PAGES = 300

# Canonical size is the portrait pair (shorter side, longer side) when the
# sheet is normally named that way. Orientation is chosen later.
PAPERS: list[dict[str, Any]] = [
    {"id": "A6", "name": "A6", "w": 105, "h": 148, "group": "iso", "common": False},
    {"id": "A5", "name": "A5", "w": 148, "h": 210, "group": "iso", "common": False},
    {"id": "A4", "name": "A4", "w": 210, "h": 297, "group": "iso", "common": True},
    {"id": "A3", "name": "A3", "w": 297, "h": 420, "group": "iso", "common": True},
    {"id": "A2", "name": "A2", "w": 420, "h": 594, "group": "iso", "common": True},
    {"id": "A1", "name": "A1", "w": 594, "h": 841, "group": "iso", "common": False},
    {"id": "A0", "name": "A0", "w": 841, "h": 1189, "group": "iso", "common": False},
    {"id": "SRA3", "name": "SRA3 / ۳۲×۴۵", "w": 320, "h": 450, "group": "sra", "common": True},
    {"id": "SRA2", "name": "SRA2", "w": 450, "h": 640, "group": "sra", "common": False},
    {"id": "SRA1", "name": "SRA1", "w": 640, "h": 900, "group": "sra", "common": False},
    {"id": "13x19", "name": "۱۳×۱۹ اینچ", "w": 330.2, "h": 482.6, "group": "digital", "common": True},
    {"id": "B3", "name": "B3", "w": 353, "h": 500, "group": "iso", "common": False},
    {"id": "B2", "name": "B2", "w": 500, "h": 707, "group": "iso", "common": False},
    {"id": "B1", "name": "B1", "w": 707, "h": 1000, "group": "iso", "common": False},
    {"id": "35x50", "name": "ورق ۳۵×۵۰", "w": 350, "h": 500, "group": "press", "common": True},
    {"id": "50x70", "name": "ورق ۵۰×۷۰", "w": 500, "h": 700, "group": "press", "common": True},
    {"id": "70x100", "name": "ورق ۷۰×۱۰۰", "w": 700, "h": 1000, "group": "press", "common": True},
    {"id": "Letter", "name": "Letter", "w": 215.9, "h": 279.4, "group": "us", "common": False},
    {"id": "Tabloid", "name": "Tabloid", "w": 279.4, "h": 431.8, "group": "us", "common": False},
]

PAPER_BY_ID = {p["id"]: p for p in PAPERS}

PRESETS = {
    "digital": ["A4", "A3", "SRA3", "13x19"],
    "offset": ["35x50", "50x70", "70x100", "SRA3"],
    "common": [p["id"] for p in PAPERS if p["common"]],
    "all": [p["id"] for p in PAPERS],
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "paper_mode": "auto",
    "paper_id": "A4",
    "custom_w": 210.0,
    "custom_h": 297.0,
    "enabled_papers": list(PRESETS["common"]),
    "h_gap": 0.0,
    "v_gap": 0.0,
    "alignment": "center",
    "rtl": False,
    "finish_a4": True,
    "finish_order": None,
    "finish_choice": None,
    "finish_min_pages": 5,
    "fit": "proportional",
    "scale": 100.0,
    "use_custom_label_size": False,
    "label_w": None,
    "label_h": None,
    "margin_mode": "auto",
    "margin": 5.0,
    "crop_enabled": True,
    "crop_length": 6.0,
    "crop_offset": 3.0,
    "crop_weight": 0.25,
    "mark_mode": "smart",
    "marks_on_empty": False,
    "use_trimbox": True,
    "use_detected_trim": True,
    "extra_trim": 0.0,
    "extra_trim_sides": None,
}


class LayoutError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def mm_to_pt(value: float) -> float:
    return value * MM_TO_PT


def pt_to_mm(value: float) -> float:
    return value * PT_TO_MM


def fmt_mm(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def r3(value: float) -> float:
    return round(float(value), 3)


def natural_key(name: str):
    parts = re.split(r"(\d+)", name or "")
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.casefold()))
    return key


def fit_count(span: float, item: float, gap: float) -> int:
    """How many items fit along a span, without reserving a trailing gap."""
    if item <= 0.05 or span <= 0:
        return 0
    gap = max(0.0, gap)
    n = int(math.floor((span + gap) / (item + gap) + 1e-6))
    while n > 0 and (n * item + max(0, n - 1) * gap) > span + 0.02:
        n -= 1
    while n < 10000 and ((n + 1) * item + n * gap) <= span + 0.02:
        n += 1
    return max(0, n)


def orientation_of(width: float, height: float) -> tuple[str, str]:
    if abs(width - height) < 0.4:
        return "square", "مربع"
    if height > width:
        return "portrait", "عمودی"
    return "landscape", "افقی"


def auto_margin_mm(settings: dict) -> float:
    if not settings.get("crop_enabled", True):
        return 0.0
    length = float(settings.get("crop_length", 6)) * PT_TO_MM
    offset = float(settings.get("crop_offset", 3)) * PT_TO_MM
    return length + offset + 1.5


def resolve_margin(settings: dict) -> float:
    if settings.get("margin_mode") == "manual":
        return max(0.0, float(settings.get("margin") or 0))
    return auto_margin_mm(settings)


def _zero_trim() -> dict[str, float]:
    return {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}


def resolve_trim(file_info: dict, settings: dict) -> tuple[dict[str, float], str]:
    trim = _zero_trim()
    source = "none"
    kind = file_info.get("kind")
    trimbox = file_info.get("trimbox")
    detected = file_info.get("detected_trim")
    if kind == "pdf" and settings.get("use_trimbox", True) and trimbox:
        trim = {k: float(trimbox.get(k, 0) or 0) for k in ("left", "top", "right", "bottom")}
        source = "trimbox"
    elif settings.get("use_detected_trim", True) and detected:
        if float(detected.get("confidence") or 0) >= 0.75:
            trim = {k: float(detected.get(k, 0) or 0) for k in ("left", "top", "right", "bottom")}
            source = "detected"
    sides = settings.get("extra_trim_sides")
    if isinstance(sides, dict):
        for key in trim:
            trim[key] += max(0.0, float(sides.get(key) or 0))
        if any(sides.get(k) for k in trim):
            source = source if source != "none" else "manual"
    else:
        extra = max(0.0, float(settings.get("extra_trim") or 0))
        if extra:
            for key in trim:
                trim[key] += extra
            source = source if source != "none" else "manual"
    return trim, source


SIZE_TOLERANCE_MM = 1.5


def same_size(width_a: float, height_a: float, width_b: float, height_b: float, tolerance: float = SIZE_TOLERANCE_MM) -> bool:
    """True when two labels can share one imposition. A swapped orientation is a different size."""
    return abs(float(width_a) - float(width_b)) <= tolerance and abs(float(height_a) - float(height_b)) <= tolerance


def effective_label_size(file_info: dict, settings: dict) -> tuple[float, float, dict, str]:
    trim, source = resolve_trim(file_info, settings)
    width = float(file_info["width_mm"]) - trim["left"] - trim["right"]
    height = float(file_info["height_mm"]) - trim["top"] - trim["bottom"]
    scale = float(settings.get("scale") or 100) / 100.0
    if scale <= 0:
        scale = 1.0
    return width * scale, height * scale, trim, source


def reference_size(files: list[dict], settings: dict) -> tuple[float, float, dict]:
    if settings.get("use_custom_label_size"):
        width = float(settings.get("label_w") or 0)
        height = float(settings.get("label_h") or 0)
        if width <= 1 or height <= 1:
            raise LayoutError("اندازه سفارشی لیبل معتبر نیست.")
        return width, height, {"id": None, "source": "custom", "trim": _zero_trim()}
    if not files:
        raise LayoutError("فایلی برای چیدمان نیست.")
    width, height, trim, source = effective_label_size(files[0], settings)
    if width <= 1 or height <= 1:
        raise LayoutError("بعد از برش، اندازه لیبل خیلی کوچک شده است.")
    return width, height, {"id": files[0].get("id"), "source": source, "trim": trim, "name": files[0].get("name")}


def expand_items(files: list[dict]) -> list[dict]:
    items = []
    for file_info in files:
        copies = int(file_info.get("copies") or 0)
        if copies < 0:
            copies = 0
        for index in range(copies):
            items.append({
                "file": file_info,
                "copy_index": index,
                "batch_id": file_info.get("batch_id") or "default",
                "batch_name": file_info.get("batch_name") or "",
            })
    return items


def batch_plan(items: list[dict], per_page: int) -> list[dict]:
    """Each folder keeps its own serial order and starts after the previous folder."""
    rows = []
    seen = None
    for index, item in enumerate(items):
        batch_id = item.get("batch_id") or "default"
        if batch_id != seen:
            rows.append({
                "id": batch_id,
                "name": item.get("batch_name") or f"پوشه {len(rows) + 1}",
                "index": len(rows),
                "start_index": index,
                "start_page": index // per_page if per_page else 0,
                "start_slot": index % per_page if per_page else 0,
                "count": 0,
            })
            seen = batch_id
        rows[-1]["count"] += 1
    return rows


def evaluate_sheet(
    name: str,
    paper_id: str | None,
    page_w: float,
    page_h: float,
    label_w: float,
    label_h: float,
    h_gap: float,
    v_gap: float,
    margin: float,
    total_copies: int,
) -> dict | None:
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin
    cols = fit_count(usable_w, label_w, h_gap)
    rows = fit_count(usable_h, label_h, v_gap)
    if cols < 1 or rows < 1:
        return None
    per_page = cols * rows
    copies = max(1, total_copies)
    pages = int(math.ceil(copies / per_page))
    grid_w = cols * label_w + (cols - 1) * h_gap
    grid_h = rows * label_h + (rows - 1) * v_gap
    page_area = page_w * page_h
    util = (grid_w * grid_h) / page_area if page_area else 0
    waste_w = page_w - grid_w
    waste_h = page_h - grid_h
    orient, orient_fa = orientation_of(page_w, page_h)
    grid_orient_mismatch = (page_w > page_h) != (grid_w > grid_h)
    return {
        "id": paper_id,
        "name": name,
        "width_mm": r3(page_w),
        "height_mm": r3(page_h),
        "orientation": orient,
        "orientation_fa": orient_fa,
        "cols": cols,
        "rows": rows,
        "per_page": per_page,
        "pages": pages,
        "grid_w": r3(grid_w),
        "grid_h": r3(grid_h),
        "waste_w": r3(waste_w),
        "waste_h": r3(waste_h),
        "utilization": round(util, 4),
        "total_area": r3(pages * page_area),
        "page_area": r3(page_area),
        "margin": r3(margin),
        "_mismatch": grid_orient_mismatch,
        "_balance": abs(waste_w - waste_h),
    }


def _candidate_sort_key(item: dict):
    return (
        item["total_area"],
        item["pages"],
        -item["per_page"],
        -item["utilization"],
        item["_balance"],
        item["_mismatch"],
        item["page_area"],
    )


def _unused_area(item: dict) -> float:
    """Leftover sheet area across the whole job. Lower is less waste."""
    grid = float(item["grid_w"]) * float(item["grid_h"])
    return float(item["pages"]) * (float(item["page_area"]) - grid)


def _waste_sort_key(item: dict):
    """Orientation key: least leftover paper, then a leftover that is not all on one side."""
    return (
        round(_unused_area(item), 1),
        item["_balance"],
        -item["utilization"],
        item["pages"],
        item["_mismatch"],
    )


def _less_waste(best: dict, other: dict) -> bool:
    """True only when the leftover sheet area is actually smaller, not just better balanced."""
    return _unused_area(best) + 1.0 < _unused_area(other)


def sheet_options(settings: dict) -> list[tuple[str, str | None, float, float]]:
    """Return (display name, paper id, width, height) for every sheet to try."""
    mode = settings.get("paper_mode") or "auto"
    if mode == "manual":
        width = float(settings.get("custom_w") or 0)
        height = float(settings.get("custom_h") or 0)
        if width < 20 or height < 20:
            raise LayoutError("اندازه کاغذ دستی معتبر نیست.")
        return [("سفارشی", "custom", width, height)]
    if mode == "orient":
        paper = PAPER_BY_ID.get(settings.get("paper_id") or "A4")
        if not paper:
            raise LayoutError("کاغذ انتخاب‌شده شناخته نشد.")
        return [
            (paper["name"], paper["id"], paper["w"], paper["h"]),
            (paper["name"], paper["id"], paper["h"], paper["w"]),
        ]
    enabled = set(settings.get("enabled_papers") or PRESETS["common"])
    options = []
    for paper in PAPERS:
        if paper["id"] not in enabled:
            continue
        options.append((paper["name"], paper["id"], paper["w"], paper["h"]))
        if abs(paper["w"] - paper["h"]) > 0.4:
            options.append((paper["name"], paper["id"], paper["h"], paper["w"]))
    if not options:
        raise LayoutError("هیچ کاغذی برای تشخیص فعال نیست.")
    return options


def _with_a4(found: list[dict], label_w: float, label_h: float, settings: dict, total_copies: int, margin: float) -> list[dict]:
    """A4 must be comparable even if its chip is off; the bindery rule may need it."""
    if any(item.get("id") == "A4" for item in found):
        return found
    paper = PAPER_BY_ID["A4"]
    h_gap = max(0.0, float(settings.get("h_gap") or 0))
    v_gap = max(0.0, float(settings.get("v_gap") or 0))
    extra = []
    for width, height in ((paper["w"], paper["h"]), (paper["h"], paper["w"])):
        item = evaluate_sheet(
            paper["name"], paper["id"], width, height, label_w, label_h, h_gap, v_gap, margin, total_copies
        )
        if item:
            extra.append(item)
    return found + extra


def apply_finish_rule(found: list[dict], settings: dict) -> tuple[dict, dict | None]:
    """Large sheets under the bindery minimum lose to A4, which makes more pages.

    A finishing shop will not cut visiting-card work when the job is under five
    sheets. The thriftiest large sheet is then the wrong choice.
    """
    ranked = sorted(found, key=_candidate_sort_key)
    best = ranked[0]
    enabled = bool(settings.get("finish_a4", True))
    try:
        minimum = int(settings.get("finish_min_pages") or 5)
    except (TypeError, ValueError):
        minimum = 5
    minimum = max(2, min(40, minimum))
    mode = settings.get("paper_mode") or "auto"
    if not enabled or best["pages"] >= minimum:
        return best, None
    if mode != "auto":
        return best, {"kind": "locked", "from": best, "min_pages": minimum}
    a4 = [item for item in ranked if item.get("id") == "A4"]
    if not a4:
        return best, {"kind": "nofit", "from": best, "min_pages": minimum}
    enough = [item for item in a4 if item["pages"] >= minimum]
    if enough:
        pick = sorted(enough, key=_candidate_sort_key)[0]
    elif best.get("id") == "A4":
        # Same paper, still under the minimum. Don't flip orientation just to add a sheet.
        return best, {"kind": "short", "from": best, "min_pages": minimum}
    else:
        pick = sorted(a4, key=_candidate_sort_key)[0]
        if pick["pages"] <= best["pages"]:
            return best, {"kind": "short", "from": best, "min_pages": minimum}
    return pick, {
        "kind": "moved",
        "from": best,
        "min_pages": minimum,
        "still_short": pick["pages"] < minimum,
    }


FINISH_ORDERS = ("none", "cut", "lam", "both")
FINISH_ORDER_FA = {
    "none": "بدون دستور صحافی",
    "cut": "فقط برش ویزیتی",
    "lam": "فقط سلفون مات",
    "both": "برش ویزیتی و سلفون مات",
}
A3_SHORT = 297.0
A3_LONG = 420.0


def resolve_finish_order(settings: dict) -> str:
    order = settings.get("finish_order")
    if order in FINISH_ORDERS:
        return order
    return "cut" if settings.get("finish_a4", True) else "none"


def _min_pages(settings: dict) -> int:
    try:
        minimum = int(settings.get("finish_min_pages") or 5)
    except (TypeError, ValueError):
        minimum = 5
    return max(2, min(40, minimum))


def can_laminate(item: dict) -> bool:
    """Matte lamination needs a sheet that can hold A3, not a long strip of the same area."""
    short, long = sorted((float(item["width_mm"]), float(item["height_mm"])))
    return short + 0.6 >= A3_SHORT and long + 0.6 >= A3_LONG


def _ensure_paper(
    found: list[dict],
    paper_id: str,
    label_w: float,
    label_h: float,
    settings: dict,
    total_copies: int,
    margin: float,
) -> list[dict]:
    if any(item.get("id") == paper_id for item in found):
        return found
    paper = PAPER_BY_ID.get(paper_id)
    if not paper:
        return found
    h_gap = max(0.0, float(settings.get("h_gap") or 0))
    v_gap = max(0.0, float(settings.get("v_gap") or 0))
    extra = []
    for width, height in ((paper["w"], paper["h"]), (paper["h"], paper["w"])):
        item = evaluate_sheet(
            paper["name"], paper["id"], width, height, label_w, label_h, h_gap, v_gap, margin, total_copies
        )
        if item:
            extra.append(item)
    return found + extra


def _best_of(items: list[dict]) -> dict:
    """Thriftiest sheet, then the orientation of that size with less leftover paper."""
    pick = sorted(items, key=_candidate_sort_key)[0]
    twins = [
        item for item in items
        if item.get("id") == pick.get("id")
        and item["orientation"] != pick["orientation"]
        and item["pages"] <= pick["pages"]
    ]
    if not twins:
        return pick
    other = min(twins, key=_waste_sort_key)
    if _waste_sort_key(other) < _waste_sort_key(pick):
        return other
    return pick


def _cut_option(found: list[dict], minimum: int) -> dict | None:
    a4 = [item for item in found if item.get("id") == "A4"]
    if not a4:
        return None
    enough = [item for item in a4 if item["pages"] >= minimum]
    return _best_of(enough or a4)


def instruction_label(order: str, paper: dict, kind: str | None, minimum: int) -> str:
    laminates = can_laminate(paper)
    cut_ok = int(paper["pages"]) >= minimum
    if kind == "conflict":
        return "پیش‌نویس"
    if order == "none":
        return "چیدمان"
    if order == "cut":
        return "برش ویزیتی" if cut_ok else "برش ویزیتی — زیر حد"
    if order == "lam":
        return "سلفون مات" if laminates else "چیدمان"
    if kind == "chose-cut":
        return "برش ویزیتی" if cut_ok else "برش ویزیتی — زیر حد"
    if kind == "chose-lam":
        return "سلفون مات"
    if laminates and cut_ok:
        return "سلفون مات و برش"
    if laminates:
        return "سلفون مات"
    if cut_ok:
        return "برش ویزیتی"
    return "چیدمان"


def _locked_finish(best: dict, order: str, minimum: int) -> dict | None:
    if order == "none":
        return None
    notes = {"min_pages": minimum, "from": best}
    if order == "cut":
        return {**notes, "kind": "locked"} if best["pages"] < minimum else None
    if order == "lam":
        return {**notes, "kind": "lam"} if can_laminate(best) else {**notes, "kind": "nolam-locked"}
    if can_laminate(best) and best["pages"] >= minimum:
        return {**notes, "kind": "both-ok"}
    if can_laminate(best):
        return {**notes, "kind": "short-lam"}
    return {**notes, "kind": "nolam-locked"}


def _select_lam(found: list[dict], minimum: int) -> tuple[dict, dict]:
    legal = [item for item in found if can_laminate(item)]
    if not legal:
        best = sorted(found, key=_candidate_sort_key)[0]
        return best, {"kind": "nolam", "from": best, "min_pages": minimum}
    pick = _best_of(legal)
    return pick, {"kind": "lam", "from": pick, "min_pages": minimum}


def _select_both(found: list[dict], settings: dict, minimum: int) -> tuple[dict, dict]:
    legal = [item for item in found if can_laminate(item)]
    enough = [item for item in legal if item["pages"] >= minimum]
    if enough:
        pick = _best_of(enough)
        return pick, {"kind": "both-ok", "from": pick, "min_pages": minimum}
    lam = _best_of(legal) if legal else None
    cut = _cut_option(found, minimum)
    if lam and cut:
        payload = {
            "cut": _public_candidate(cut),
            "lam": _public_candidate(lam),
            "min_pages": minimum,
        }
        choice = settings.get("finish_choice")
        if choice == "cut":
            return cut, {"kind": "chose-cut", **payload}
        if choice == "lam":
            return lam, {"kind": "chose-lam", **payload}
        return lam, {"kind": "conflict", **payload}
    if lam:
        kind = "short-lam" if lam["pages"] < minimum else "lam"
        return lam, {"kind": kind, "from": lam, "min_pages": minimum}
    best = cut or sorted(found, key=_candidate_sort_key)[0]
    return best, {"kind": "nolam", "from": best, "min_pages": minimum}


def select_finish(found: list[dict], settings: dict) -> tuple[dict, dict | None]:
    order = resolve_finish_order(settings)
    mode = settings.get("paper_mode") or "auto"
    minimum = _min_pages(settings)
    if mode != "auto":
        best = sorted(found, key=_waste_sort_key)[0] if mode == "orient" else found[0]
        return best, _locked_finish(best, order, minimum)
    if order == "none":
        return sorted(found, key=_candidate_sort_key)[0], None
    if order == "cut":
        return apply_finish_rule(found, {**settings, "finish_a4": True})
    if order == "lam":
        return _select_lam(found, minimum)
    return _select_both(found, settings, minimum)


def choose_paper(
    label_w: float,
    label_h: float,
    settings: dict,
    total_copies: int,
    margin: float,
) -> tuple[dict, list[dict]]:
    h_gap = max(0.0, float(settings.get("h_gap") or 0))
    v_gap = max(0.0, float(settings.get("v_gap") or 0))
    found = []
    for name, paper_id, width, height in sheet_options(settings):
        item = evaluate_sheet(
            name, paper_id, width, height, label_w, label_h, h_gap, v_gap, margin, total_copies
        )
        if item:
            found.append(item)
    if not found:
        raise LayoutError(
            f"لیبل {fmt_mm(label_w)}×{fmt_mm(label_h)} میلی‌متر با حاشیه {fmt_mm(margin)} میلی‌متر "
            "در کاغذهای فعال جا نمی‌شود. کاغذ بزرگ‌تری را روشن کنید یا حاشیه را کم کنید."
        )
    mode = settings.get("paper_mode") or "auto"
    order = resolve_finish_order(settings)
    if mode == "auto":
        if order in ("cut", "both"):
            found = _ensure_paper(found, "A4", label_w, label_h, settings, total_copies, margin)
        if order in ("lam", "both"):
            found = _ensure_paper(found, "A3", label_w, label_h, settings, total_copies, margin)
    best, finish = select_finish(found, settings)
    if finish:
        best = dict(best)
        best["_finish"] = finish
    found.sort(key=_waste_sort_key if mode == "orient" else _candidate_sort_key)
    return best, found


def grid_offsets(page_w: float, page_h: float, grid_w: float, grid_h: float, margin: float, alignment: str) -> tuple[float, float]:
    safe_w = page_w - 2 * margin
    safe_h = page_h - 2 * margin
    extra_x = max(0.0, safe_w - grid_w)
    extra_y = max(0.0, safe_h - grid_h)
    alignment = alignment or "center"
    if alignment == "topLeft":
        ox, oy = 0.0, 0.0
    elif alignment == "topRight":
        ox, oy = extra_x, 0.0
    elif alignment == "bottomLeft":
        ox, oy = 0.0, extra_y
    elif alignment == "bottomRight":
        ox, oy = extra_x, extra_y
    else:
        ox, oy = extra_x / 2, extra_y / 2
    return margin + ox, margin + oy


def _seg_len(seg: tuple[float, float, float, float]) -> float:
    return math.hypot(seg[2] - seg[0], seg[3] - seg[1])


def corner_marks(x1: float, y1: float, x2: float, y2: float, length: float, offset: float) -> list[tuple[float, float, float, float]]:
    """Eight crop marks around a trim rectangle. Origin is top-left, y grows down."""
    return [
        (x1 - offset - length, y1, x1 - offset, y1),
        (x1, y1 - offset - length, x1, y1 - offset),
        (x2 + offset, y1, x2 + offset + length, y1),
        (x2, y1 - offset - length, x2, y1 - offset),
        (x1 - offset - length, y2, x1 - offset, y2),
        (x1, y2 + offset, x1, y2 + offset + length),
        (x2 + offset, y2, x2 + offset + length, y2),
        (x2, y2 + offset, x2, y2 + offset + length),
    ]


def _normalize(seg: tuple[float, float, float, float]) -> tuple[tuple[float, float, float, float], bool]:
    x1, y1, x2, y2 = seg
    flipped = (x1, y1) > (x2, y2)
    if flipped:
        return (x2, y2, x1, y1), True
    return (x1, y1, x2, y2), False


def _restore(seg: tuple[float, float, float, float], flipped: bool) -> tuple[float, float, float, float]:
    if flipped:
        return (seg[2], seg[3], seg[0], seg[1])
    return seg


def subtract_rect(
    seg: tuple[float, float, float, float],
    rect: tuple[float, float, float, float],
    eps: float,
) -> list[tuple[float, float, float, float]]:
    """Remove the part of an axis-aligned segment that enters a rectangle interior."""
    (x1, y1, x2, y2), flipped = _normalize(seg)
    left, top, right, bottom = rect
    left += eps
    top += eps
    right -= eps
    bottom -= eps
    if right <= left or bottom <= top:
        return [seg]
    horizontal = abs(y2 - y1) <= 1e-4
    vertical = abs(x2 - x1) <= 1e-4
    if not horizontal and not vertical:
        return [seg]
    parts: list[tuple[float, float, float, float]] = []
    if horizontal:
        y = (y1 + y2) / 2
        if y <= top or y >= bottom or x2 <= left or x1 >= right:
            return [seg]
        if x1 < left:
            parts.append((x1, y, min(x2, left), y))
        if x2 > right:
            parts.append((max(x1, right), y, x2, y))
    else:
        x = (x1 + x2) / 2
        if x <= left or x >= right or y2 <= top or y1 >= bottom:
            return [seg]
        if y1 < top:
            parts.append((x, y1, x, min(y2, top)))
        if y2 > bottom:
            parts.append((x, max(y1, bottom), x, y2))
    return [_restore(part, flipped) for part in parts if _seg_len(part) >= 0.25]


def clip_seg_to_rect(
    seg: tuple[float, float, float, float],
    rect: tuple[float, float, float, float],
    min_len: float = 0.35,
) -> tuple[float, float, float, float] | None:
    (x1, y1, x2, y2), flipped = _normalize(seg)
    left, top, right, bottom = rect
    horizontal = abs(y2 - y1) <= 1e-4
    vertical = abs(x2 - x1) <= 1e-4
    if horizontal:
        y = (y1 + y2) / 2
        if y < top or y > bottom:
            return None
        xa = max(x1, left)
        xb = min(x2, right)
        if xb - xa < min_len:
            return None
        return _restore((xa, y, xb, y), flipped)
    if vertical:
        x = (x1 + x2) / 2
        if x < left or x > right:
            return None
        ya = max(y1, top)
        yb = min(y2, bottom)
        if yb - ya < min_len:
            return None
        return _restore((x, ya, x, yb), flipped)
    return None


def intersect_seg_rect(
    seg: tuple[float, float, float, float],
    rect: tuple[float, float, float, float],
    eps: float,
) -> tuple[float, float, float, float] | None:
    (x1, y1, x2, y2), flipped = _normalize(seg)
    left, top, right, bottom = rect
    left += eps
    top += eps
    right -= eps
    bottom -= eps
    if right <= left or bottom <= top:
        return None
    if abs(y2 - y1) <= 1e-4:
        y = (y1 + y2) / 2
        if y <= top or y >= bottom:
            return None
        xa = max(x1, left)
        xb = min(x2, right)
        if xb - xa < 0.2:
            return None
        return _restore((xa, y, xb, y), flipped)
    if abs(x2 - x1) <= 1e-4:
        x = (x1 + x2) / 2
        if x <= left or x >= right:
            return None
        ya = max(y1, top)
        yb = min(y2, bottom)
        if yb - ya < 0.2:
            return None
        return _restore((x, ya, x, yb), flipped)
    return None


def _dedupe(segments: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    seen = set()
    result = []
    for seg in segments:
        (x1, y1, x2, y2), _ = _normalize(seg)
        key = (round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2))
        if key in seen:
            continue
        seen.add(key)
        result.append(seg)
    return result


def build_crop_marks(
    rects: list[tuple[float, float, float, float]],
    length: float,
    offset: float,
    page_w: float,
    page_h: float,
    mode: str = "smart",
) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float, float, float]]]:
    """Return (kept marks, removed portions).

    Smart mode draws a mark at every label corner, then deletes any part that
    would run into another label. Butted labels therefore lose the marks that
    used to sit in the middle, while outer marks stay.
    """
    if not rects:
        return [], []
    if mode == "block":
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[2] for r in rects)
        bottom = max(r[3] for r in rects)
        sources = [(left, top, right, bottom)]
        obstacles: list[tuple[float, float, float, float]] = []
    else:
        sources = rects
        obstacles = rects

    raw: list[tuple[float, float, float, float]] = []
    for rect in sources:
        raw.extend(corner_marks(rect[0], rect[1], rect[2], rect[3], length, offset))

    page = (0.6, 0.6, page_w - 0.6, page_h - 0.6)
    kept: list[tuple[float, float, float, float]] = []
    removed: list[tuple[float, float, float, float]] = []
    # Negative epsilon expands each label a hair so a mark lying on the
    # shared trim edge — the usual "middle" mark when labels are butted —
    # counts as hitting the neighbour, not only marks that enter the artwork.
    eps = -0.08
    for seg in raw:
        parts = [seg]
        for obstacle in obstacles:
            nxt = []
            for part in parts:
                nxt.extend(subtract_rect(part, obstacle, eps))
            parts = nxt
        clipped = []
        for part in parts:
            piece = clip_seg_to_rect(part, page)
            if piece and _seg_len(piece) >= 0.35:
                clipped.append(piece)
        if not clipped or _seg_len(clipped[0]) < _seg_len(seg) - 0.4 or len(clipped) != 1:
            # Something was cut away. Record the portion that hit a label,
            # not the part merely clipped by the page edge.
            hit_any = False
            for obstacle in obstacles:
                piece = intersect_seg_rect(seg, obstacle, eps)
                if piece:
                    removed.append(piece)
                    hit_any = True
            if not clipped and not hit_any:
                removed.append(seg)
        kept.extend(clipped)
    return _dedupe(kept), _dedupe(removed)


def content_rect(cell: tuple[float, float, float, float], content_w: float, content_h: float, fit: str) -> tuple[float, float, float, float]:
    x, y, w, h = cell
    if content_w <= 0 or content_h <= 0 or w <= 0 or h <= 0:
        return cell
    fit = fit or "proportional"
    cell_ratio = w / h
    content_ratio = content_w / content_h
    if fit == "center":
        return (x + (w - content_w) / 2, y + (h - content_h) / 2, content_w, content_h)
    if fit == "fill":
        if content_ratio > cell_ratio:
            draw_h = h
            draw_w = h * content_ratio
        else:
            draw_w = w
            draw_h = w / content_ratio
        return (x + (w - draw_w) / 2, y + (h - draw_h) / 2, draw_w, draw_h)
    if content_ratio > cell_ratio:
        draw_w = w
        draw_h = w / content_ratio
    else:
        draw_h = h
        draw_w = h * content_ratio
    return (x + (w - draw_w) / 2, y + (h - draw_h) / 2, draw_w, draw_h)


def _public_candidate(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _sheet_phrase(item: dict) -> str:
    return (
        f"{item['name']} {item['orientation_fa']}، "
        f"{item['cols']}×{item['rows']}، {item['per_page']} لیبل در برگ، {item['pages']} برگ"
    )


def explain_choice(best: dict, others: list[dict], total: int) -> str:
    finish = best.get("_finish") or {}
    kind = finish.get("kind")
    minimum = finish.get("min_pages") or 5
    if kind == "conflict":
        cut, lam = finish["cut"], finish["lam"]
        return (
            f"برش ویزیتی و سلفون مات با هم جور نشد. "
            f"{lam['name']} {lam['orientation_fa']} فقط {lam['pages']} برگ می‌شود و زیر {minimum} است. "
            f"{cut['name']} {cut['orientation_fa']} {cut['pages']} برگ می‌شود، ولی سلفون مات نمی‌گیرد. "
            "تا یکی را انتخاب نکنید این برگ نهایی نیست."
        )
    if kind == "chose-cut":
        cut = finish["cut"]
        return (
            f"با انتخاب شما برش ویزیتی ماند: {_sheet_phrase(cut)}. "
            "سلفون مات روی این اندازه انجام نمی‌شود."
        )
    if kind == "chose-lam":
        lam = finish["lam"]
        return (
            f"با انتخاب شما سلفون مات ماند: {_sheet_phrase(lam)}. "
            f"این جهت پرتی کمتری دارد. صحافی زیر {minimum} برگ برش ویزیتی نمی‌گیرد."
        )
    if kind == "both-ok":
        return (
            f"برش ویزیتی و سلفون مات هر دو ممکن بود. {_sheet_phrase(best)} "
            f"و از حد {minimum} برگ رد می‌شود."
        )
    if kind == "lam":
        text = (
            f"سلفون مات روی A4 انجام نمی‌شود، برای همین {_sheet_phrase(best)} انتخاب شد."
        )
        twin = next((item for item in others if item.get("id") == best.get("id") and item["orientation"] != best["orientation"]), None)
        if twin and _less_waste(best, twin):
            text += (
                f" جهت {'افقی' if twin['orientation'] == 'landscape' else 'عمودی'} پرتی بیشتری داشت: "
                f"{fmt_mm(twin['waste_w'])}×{fmt_mm(twin['waste_h'])} در برابر "
                f"{fmt_mm(best['waste_w'])}×{fmt_mm(best['waste_h'])} میلی‌متر."
            )
        elif twin:
            text += f" جهت {'افقی' if twin['orientation'] == 'landscape' else 'عمودی'} هم همین تعداد را می‌گرفت؛ این حاشیه متعادل‌تری دارد."
        return text
    if kind in ("nolam", "nolam-locked"):
        return (
            f"سلفون مات باید روی A3 یا بزرگ‌تر باشد. "
            f"{best['name']} {best['orientation_fa']} آن اندازه را ندارد."
        )
    if kind == "short-lam":
        return (
            f"{_sheet_phrase(best)}. سلفون مات ممکن است، "
            f"ولی زیر {minimum} برگ برش ویزیتی نمی‌گیرد و اندازه کاغذ قفل است."
        )
    if kind == "moved":
        src = finish["from"]
        text = (
            f"ورق {src['name']} {src['orientation_fa']} فقط {src['pages']} برگ می‌شد. "
            f"صحافی زیر {finish['min_pages']} برگ برش ویزیتی نمی‌گیرد، "
            f"برای همین {best['name']} {best['orientation_fa']} انتخاب شد تا {best['pages']} برگ شود: "
            f"{best['cols']}×{best['rows']}، {best['per_page']} لیبل در هر برگ."
        )
        if finish.get("still_short"):
            text += f" با این حال هنوز زیر {finish['min_pages']} برگ است."
        waste = (1 - best["utilization"]) * 100
        text += f" پرتی سطح برگ حدود {waste:.0f}٪ است."
        return text
    same_paper = [
        item for item in others
        if item is not best and item.get("id") == best.get("id") and item["orientation"] != best["orientation"]
    ]
    other = same_paper[0] if same_paper else None
    lower_waste = bool(other) and _less_waste(best, other)
    if lower_waste:
        text = (
            f"برای {total} لیبل، {best['name']} {best['orientation_fa']} کمترین پرتی را دارد: "
            f"{best['cols']}×{best['rows']} یعنی {best['per_page']} لیبل در هر برگ، {best['pages']} برگ. "
            f"پرتی {fmt_mm(best['waste_w'])}×{fmt_mm(best['waste_h'])} میلی‌متر است."
        )
    else:
        text = (
            f"برای {total} لیبل، {best['name']} {best['orientation_fa']} کمترین مصرف کاغذ را دارد: "
            f"{best['cols']}×{best['rows']} یعنی {best['per_page']} لیبل در هر برگ، {best['pages']} برگ."
        )
    if other:
        other_name = "افقی" if other["orientation"] == "landscape" else "عمودی"
        if lower_waste:
            text += (
                f" جهت {other_name} پرتی بیشتری داشت: "
                f"{fmt_mm(other['waste_w'])}×{fmt_mm(other['waste_h'])} میلی‌متر."
            )
        elif other["pages"] > best["pages"] or other["per_page"] < best["per_page"]:
            text += f" جهت {other_name} فقط {other['per_page']} لیبل در برگ جا می‌داد"
            if other["pages"] > best["pages"]:
                text += f" و {other['pages']} برگ لازم داشت"
            text += "."
        else:
            text += f" جهت {other_name} هم {other['per_page']} لیبل می‌گرفت؛ {best['orientation_fa']} حاشیه متعادل‌تری دارد."
    waste = (1 - best["utilization"]) * 100
    text += f" پرتی سطح برگ حدود {waste:.0f}٪ است."
    return text


def public_finish(best: dict, settings: dict) -> dict:
    order = resolve_finish_order(settings)
    finish = best.get("_finish") or {}
    kind = finish.get("kind")
    minimum = int(finish.get("min_pages") or _min_pages(settings))
    choice = "cut" if kind == "chose-cut" else "lam" if kind == "chose-lam" else None
    conflict = None
    if finish.get("cut") and finish.get("lam"):
        conflict = {"cut": finish["cut"], "lam": finish["lam"]}
    return {
        "order": order,
        "order_fa": FINISH_ORDER_FA[order],
        "kind": kind,
        "choice": choice,
        "resolved": kind != "conflict",
        "min_pages": minimum,
        "label": instruction_label(order, best, kind, minimum),
        "conflict": conflict,
    }


def _slug_rect(edge: str, thickness: float, page_w: float, page_h: float, ox: float, oy: float, grid_w: float, grid_h: float, mark_pad: float, outside: bool) -> dict:
    inset = 0.7
    thickness = max(0.0, thickness)
    if edge == "bottom":
        y0 = (oy + grid_h + mark_pad) if outside else (oy + grid_h)
        rect = (inset, y0, page_w - 2 * inset, thickness)
        rotate = 0
    elif edge == "top":
        rect = (inset, 0.0, page_w - 2 * inset, thickness)
        rotate = 0
    elif edge == "left":
        rect = (0.0, inset, thickness, page_h - 2 * inset)
        rotate = 90
    else:
        rect = (page_w - thickness, inset, thickness, page_h - 2 * inset)
        rotate = 270
    short = min(rect[2], rect[3]) if rect[2] and rect[3] else 0
    size_pt = min(7.0, max(4.5, (short - 0.35) / PT_TO_MM)) if short else 6.0
    return {
        "edge": edge,
        "x": r3(rect[0]),
        "y": r3(rect[1]),
        "w": r3(max(rect[2], 0)),
        "h": r3(max(rect[3], 0)),
        "rotate": rotate,
        "outside_marks": outside,
        "size_pt": round(size_pt, 2),
    }


def slug_band(
    page_w: float,
    page_h: float,
    ox: float,
    oy: float,
    grid_w: float,
    grid_h: float,
    mark_pad: float,
) -> dict:
    """Keep the instruction outside the cut line, horizontal when a line fits."""
    clear = {
        "bottom": page_h - (oy + grid_h),
        "top": oy,
        "right": page_w - (ox + grid_w),
        "left": ox,
    }
    for edge in ("bottom", "top"):
        if clear[edge] - mark_pad >= 2.6:
            return _slug_rect(edge, clear[edge] - mark_pad, page_w, page_h, ox, oy, grid_w, grid_h, mark_pad, True)
        if clear[edge] >= 2.8:
            return _slug_rect(edge, clear[edge], page_w, page_h, ox, oy, grid_w, grid_h, mark_pad, False)
    edge = max(("right", "left"), key=lambda name: clear[name])
    outside = clear[edge] - mark_pad >= 2.4
    thickness = clear[edge] - mark_pad if outside else clear[edge]
    return _slug_rect(edge, thickness, page_w, page_h, ox, oy, grid_w, grid_h, mark_pad, outside)


def make_slug(
    best: dict,
    settings: dict,
    page_w: float,
    page_h: float,
    ox: float,
    oy: float,
    grid_w: float,
    grid_h: float,
    mark_pad: float,
) -> dict:
    order = resolve_finish_order(settings)
    finish = best.get("_finish") or {}
    minimum = int(finish.get("min_pages") or _min_pages(settings))
    label = instruction_label(order, best, finish.get("kind"), minimum)
    band = slug_band(page_w, page_h, ox, oy, grid_w, grid_h, mark_pad)
    band["base"] = f"{label} · {best['name']} {best['orientation_fa']}"
    return band


def compute_layout(files: list[dict], settings: dict | None = None) -> dict:
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    warnings: list[str] = []
    if not files:
        raise LayoutError("اول فایل لیبل را اضافه کنید.")
    items = expand_items(files)
    total = len(items)
    if total == 0:
        raise LayoutError("هیچ کپی‌ای برای چیدمان انتخاب نشده. تعداد را بیشتر از صفر بگذارید.")

    label_w, label_h, ref = reference_size(files, settings)
    sizes = []
    prepared = []
    for file_info in files:
        width, height, trim, source = effective_label_size(file_info, settings)
        prepared.append({"file": file_info, "width": width, "height": height, "trim": trim, "source": source})
        sizes.append((width, height))
        if abs(width - label_w) > 1.5 or abs(height - label_h) > 1.5:
            warnings.append(
                f"«{file_info.get('name')}» بعد از برش {fmt_mm(width)}×{fmt_mm(height)} است، "
                f"ولی قاب‌ها روی {fmt_mm(label_w)}×{fmt_mm(label_h)} ساخته می‌شوند."
            )
    if len(warnings) > 4:
        extra = len(warnings) - 4
        warnings = warnings[:4] + [f"و {extra} فایل دیگر اندازه متفاوت دارند."]

    margin = resolve_margin(settings)
    best, candidates = choose_paper(label_w, label_h, settings, total, margin)
    finish = best.get("_finish") or {}
    if finish.get("kind") == "moved" and finish.get("still_short"):
        warnings.append(
            f"حتی روی {best['name']} هم {best['pages']} برگ است و زیر {finish['min_pages']} برگ. "
            "صحافی ممکن است برش ویزیتی نگیرد."
        )
    elif finish.get("kind") == "short":
        src = finish["from"]
        warnings.append(
            f"{src['name']} {src['orientation_fa']} فقط {src['pages']} برگ است. "
            f"صحافی زیر {finish['min_pages']} برگ برش ویزیتی نمی‌گیرد و A4 برگ بیشتری نمی‌سازد."
        )
    elif finish.get("kind") == "nofit":
        src = finish["from"]
        warnings.append(
            f"{src['name']} {src['orientation_fa']} فقط {src['pages']} برگ است. "
            f"صحافی زیر {finish['min_pages']} برگ برش ویزیتی نمی‌گیرد و لیبل در A4 جا نمی‌شود."
        )
    elif finish.get("kind") == "locked":
        src = finish["from"]
        warnings.append(
            f"{src['name']} {src['orientation_fa']} فقط {src['pages']} برگ است. "
            f"صحافی زیر {finish['min_pages']} برگ برش ویزیتی نمی‌گیرد. اگر ممکن است روی A4 بچینید."
        )
    elif finish.get("kind") == "conflict":
        warnings.append("برش ویزیتی و سلفون مات با هم جور نشد. تا یکی را انتخاب نکنید این برگ نهایی نیست.")
    elif finish.get("kind") == "chose-cut":
        warnings.append("سلفون مات انجام نمی‌شود؛ با انتخاب شما برش ویزیتی ماند.")
    elif finish.get("kind") == "chose-lam":
        warnings.append(
            f"با انتخاب شما سلفون مات ماند. زیر {finish['min_pages']} برگ برش ویزیتی نمی‌گیرد."
        )
    elif finish.get("kind") in ("nolam", "nolam-locked"):
        warnings.append("سلفون مات باید روی A3 یا بزرگ‌تر باشد و این برگ آن اندازه را ندارد.")
    elif finish.get("kind") == "short-lam":
        src = finish.get("from") or best
        warnings.append(
            f"{src['name']} {src['orientation_fa']} فقط {src['pages']} برگ است. "
            f"سلفون مات ممکن است، ولی زیر {finish['min_pages']} برگ برش ویزیتی نمی‌گیرد."
        )
    page_w = best["width_mm"]
    page_h = best["height_mm"]
    cols = best["cols"]
    rows = best["rows"]
    h_gap = max(0.0, float(settings.get("h_gap") or 0))
    v_gap = max(0.0, float(settings.get("v_gap") or 0))
    grid_w = cols * label_w + (cols - 1) * h_gap
    grid_h = rows * label_h + (rows - 1) * v_gap
    ox, oy = grid_offsets(page_w, page_h, grid_w, grid_h, margin, settings.get("alignment") or "center")
    per_page = cols * rows
    pages_needed = int(math.ceil(total / per_page))
    if pages_needed > MAX_PAGES:
        raise LayoutError(f"این کار {pages_needed} صفحه می‌شود. تعداد کپی را کم کنید یا کاغذ بزرگ‌تری انتخاب کنید.")
    if pages_needed > 80:
        warnings.append(f"خروجی {pages_needed} صفحه است و ممکن است سنگین شود.")

    if abs(float(settings.get("scale") or 100) - 100) > 0.1:
        warnings.append("مقیاس ۱۰۰٪ نیست. بارکد و سریال ممکن است کش بیاید یا ناخوانا شود.")
    seen_batches = set()
    for item in prepared:
        batch_id = item["file"].get("batch_id") or "default"
        if batch_id in seen_batches:
            continue
        seen_batches.add(batch_id)
        if abs(item["width"] - label_w) > 1.5 or abs(item["height"] - label_h) > 1.5:
            batch_name = item["file"].get("batch_name") or item["file"].get("name")
            warnings.append(
                f"پوشه «{batch_name}» هم‌اندازه مدل اول نیست "
                f"({fmt_mm(item['width'])}×{fmt_mm(item['height'])} میلی‌متر). "
                "قاب همان اندازه اول می‌ماند."
            )

    # Every sheet reads the same way: top row from the left, then the next row from the left.
    rtl = False
    fit = settings.get("fit") or "proportional"
    length = float(settings.get("crop_length", 6)) * PT_TO_MM
    offset = float(settings.get("crop_offset", 3)) * PT_TO_MM
    crop_on = bool(settings.get("crop_enabled", True))
    mark_mode = settings.get("mark_mode") or "smart"
    marks_on_empty = bool(settings.get("marks_on_empty"))

    size_by_id = {item["file"].get("id"): item for item in prepared}
    batches = batch_plan(items, per_page)
    batch_at = {}
    for row in batches:
        batch_at[row["start_index"]] = row

    def batch_of(slot: int) -> dict:
        current = batches[0]
        for row in batches:
            if row["start_index"] <= slot:
                current = row
            else:
                break
        return current

    pages = []
    kept_total = 0
    removed_total = 0
    for page_index in range(pages_needed):
        cells = []
        placed_rects = []
        all_rects = []
        for slot in range(per_page):
            row = slot // cols
            col = slot % cols
            visual_col = (cols - 1 - col) if rtl else col
            x = ox + visual_col * (label_w + h_gap)
            y = oy + row * (label_h + v_gap)
            rect = (x, y, x + label_w, y + label_h)
            all_rects.append(rect)
            item_index = page_index * per_page + slot
            if item_index >= total:
                cells.append({
                    "x": r3(x), "y": r3(y), "w": r3(label_w), "h": r3(label_h),
                    "empty": True, "file_id": None, "content": None,
                })
                continue
            item = items[item_index]
            file_info = item["file"]
            meta = size_by_id.get(file_info.get("id"))
            cw, ch = (meta["width"], meta["height"]) if meta else (label_w, label_h)
            cx, cy, cw, ch = content_rect((x, y, label_w, label_h), cw, ch, fit)
            placed_rects.append(rect)
            batch = batch_of(item_index)
            cells.append({
                "x": r3(x), "y": r3(y), "w": r3(label_w), "h": r3(label_h),
                "empty": False,
                "file_id": file_info.get("id"),
                "name": file_info.get("name"),
                "copy_index": item["copy_index"],
                "content": {"x": r3(cx), "y": r3(cy), "w": r3(cw), "h": r3(ch)},
                "trim": meta["trim"] if meta else _zero_trim(),
                "trim_source": meta["source"] if meta else "none",
                "batch_id": batch["id"],
                "batch_name": batch["name"],
                "batch_index": batch["index"],
                "batch_start": item_index in batch_at,
            })
        mark_rects = all_rects if marks_on_empty else placed_rects
        kept, removed = ([], [])
        if crop_on:
            kept, removed = build_crop_marks(mark_rects, length, offset, page_w, page_h, mark_mode)
        kept_total += len(kept)
        removed_total += len(removed)
        pages.append({
            "index": page_index,
            "cells": cells,
            "marks": [_seg_dict(seg, False) for seg in kept],
            "removed_marks": [_seg_dict(seg, True) for seg in removed],
        })

    if crop_on and margin + 0.2 < (length + offset) and settings.get("margin_mode") == "manual":
        warnings.append("حاشیه دستی از طول کراپ‌مارک کوتاه‌تر است؛ ممکن است بخشی از مارک بیرون صفحه بیفتد و بریده شود.")

    reason = explain_choice(best, candidates, total)
    mark_pad = (length + offset) if crop_on else 0.0
    slug = make_slug(best, settings, page_w, page_h, ox, oy, grid_w, grid_h, mark_pad)
    finish_public = public_finish(best, settings)
    file_sizes = []
    for item in prepared:
        file_sizes.append({
            "id": item["file"].get("id"),
            "width_mm": r3(item["width"]),
            "height_mm": r3(item["height"]),
            "trim": {k: r3(v) for k, v in item["trim"].items()},
            "trim_source": item["source"],
        })
    return {
        "ok": True,
        "paper": _public_candidate(best),
        "reason": reason,
        "finish": finish_public,
        "slug": slug,
        "warnings": warnings,
        "reference": {
            "width_mm": r3(label_w),
            "height_mm": r3(label_h),
            "file_id": ref.get("id"),
            "name": ref.get("name"),
            "source": ref.get("source"),
        },
        "grid": {
            "cols": cols,
            "rows": rows,
            "per_page": per_page,
            "h_gap": r3(h_gap),
            "v_gap": r3(v_gap),
            "label_w": r3(label_w),
            "label_h": r3(label_h),
            "grid_w": r3(grid_w),
            "grid_h": r3(grid_h),
            "offset_x": r3(ox),
            "offset_y": r3(oy),
            "margin": r3(margin),
            "waste_w": r3(page_w - grid_w),
            "waste_h": r3(page_h - grid_h),
            "utilization": best["utilization"],
            "rtl": rtl,
            "alignment": settings.get("alignment") or "center",
        },
        "stats": {
            "total_copies": total,
            "pages": pages_needed,
            "marks_kept": kept_total,
            "marks_removed": removed_total,
            "unique_files": len(files),
            "sheet_area_cm2": round(best["page_area"] / 100, 1),
            "job_area_cm2": round(best["total_area"] / 100, 1),
        },
        "candidates": [_public_candidate(item) for item in candidates[:8]],
        "files": file_sizes,
        "batches": batches,
        "pages": pages,
        "crop": {
            "enabled": crop_on,
            "length_pt": float(settings.get("crop_length", 6)),
            "offset_pt": float(settings.get("crop_offset", 3)),
            "weight_pt": float(settings.get("crop_weight", 0.25)),
            "mode": mark_mode,
        },
    }


def _seg_dict(seg: tuple[float, float, float, float], removed: bool) -> dict:
    return {
        "x1": r3(seg[0]), "y1": r3(seg[1]), "x2": r3(seg[2]), "y2": r3(seg[3]),
        "removed": removed,
    }


def _runs(mask) -> list[tuple[int, int]]:
    runs = []
    start = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def detect_crop_margins(image, dpi: float) -> dict | None:
    """Find classic outward corner crop marks on a light margin.

    Returns margins in millimetres plus a confidence score, or None.
    A frame around the artwork is ignored: crop marks point outward and
    are separated from the trim corner, a border points inward.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    if image is None or dpi <= 1:
        return None
    if not isinstance(image, Image.Image):
        return None
    image = image.convert("RGB")
    width, height = image.size
    scale = 1.0
    if max(width, height) > 1400:
        scale = 1400 / max(width, height)
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.BOX)
    arr = np.asarray(image)
    lum = arr.mean(axis=2)
    sat = arr.max(axis=2) - arr.min(axis=2)
    dark = (lum < 95) & (sat < 40)
    white = lum > 228
    px_per_mm = (dpi / 25.4) * scale
    if px_per_mm < 0.8:
        return None
    band = int(round(18 * px_per_mm))
    band = max(8, min(band, min(arr.shape[0], arr.shape[1]) // 3))
    min_len = max(3, int(round(1.3 * px_per_mm)))
    max_len = int(round(16 * px_per_mm))
    max_thick = max(2, int(round(0.7 * px_per_mm)))

    def thin_enough(y: int, a: int, b: int, axis: str) -> bool:
        if b - a < min_len:
            return False
        white_hits = []
        dark_rows = 0
        for delta in range(-(max_thick + 1), max_thick + 2):
            if axis == "h":
                yy = y + delta
                if not (0 <= yy < dark.shape[0]):
                    continue
                portion = dark[yy, a:b]
                surround = white[yy, a:b]
            else:
                xx = y + delta
                if not (0 <= xx < dark.shape[1]):
                    continue
                portion = dark[a:b, xx]
                surround = white[a:b, xx]
            if abs(delta) <= max_thick and float(portion.mean()) > 0.55:
                dark_rows += 1
            if abs(delta) in (1, 2):
                white_hits.append(float(surround.mean()))
        if dark_rows > max_thick:
            return False
        return bool(white_hits) and max(white_hits) > 0.62

    h_marks = []
    v_marks = []
    height_s, width_s = dark.shape
    for y in range(height_s):
        for a, b in _runs(dark[y]):
            if min_len <= (b - a) <= max_len and thin_enough(y, a, b, "h"):
                h_marks.append((a, y, b, y))
    for x in range(width_s):
        for a, b in _runs(dark[:, x]):
            if min_len <= (b - a) <= max_len and thin_enough(x, a, b, "v"):
                v_marks.append((x, a, x, b))

    corners = {
        "tl": (0, band, 0, band),
        "tr": (width_s - band, width_s, 0, band),
        "bl": (0, band, height_s - band, height_s),
        "br": (width_s - band, width_s, height_s - band, height_s),
    }
    votes = []
    min_margin = 1.4 * px_per_mm
    max_margin = 18 * px_per_mm
    max_offset = 8 * px_per_mm
    for name, (x0, x1, y0, y1) in corners.items():
        hs = [m for m in h_marks if x0 <= m[0] and m[2] <= x1 and y0 <= m[1] <= y1]
        vs = [m for m in v_marks if y0 <= m[1] and m[3] <= y1 and x0 <= m[0] <= x1]
        best = None
        for hx1, hy, hx2, _ in hs:
            for vx, vy1, _, vy2 in vs:
                if name in ("tl", "bl") and hx2 > vx + 0.45 * px_per_mm:
                    continue
                if name in ("tr", "br") and hx1 < vx - 0.45 * px_per_mm:
                    continue
                if name in ("tl", "tr") and vy2 > hy + 0.45 * px_per_mm:
                    continue
                if name in ("bl", "br") and vy1 < hy - 0.45 * px_per_mm:
                    continue
                gap_x = (vx - hx2) if name in ("tl", "bl") else (hx1 - vx)
                gap_y = (hy - vy2) if name in ("tl", "tr") else (vy1 - hy)
                if gap_x < -0.45 * px_per_mm or gap_y < -0.45 * px_per_mm:
                    continue
                if gap_x > max_offset or gap_y > max_offset:
                    continue
                if name in ("tl", "bl"):
                    left = vx
                else:
                    left = width_s - vx
                if name in ("tl", "tr"):
                    top = hy
                else:
                    top = height_s - hy
                if not (min_margin <= left <= max_margin and min_margin <= top <= max_margin):
                    continue
                score = abs(gap_x - gap_y)
                if best is None or score < best[0]:
                    best = (score, left / px_per_mm / scale * scale, top / px_per_mm)
                    # px_per_mm already includes scale, so divide once.
                    best = (score, left / px_per_mm, top / px_per_mm)
        if best:
            votes.append((name, best[1], best[2]))

    if len(votes) < 3:
        return None
    by_name = {name: (left, top) for name, left, top in votes}
    lefts = [by_name[n][0] for n in ("tl", "bl") if n in by_name]
    rights = [by_name[n][0] for n in ("tr", "br") if n in by_name]
    tops = [by_name[n][1] for n in ("tl", "tr") if n in by_name]
    bottoms = [by_name[n][1] for n in ("bl", "br") if n in by_name]
    if not lefts or not rights or not tops or not bottoms:
        return None

    def agree(values: list[float]) -> float | None:
        if max(values) - min(values) > 1.6:
            return None
        return sum(values) / len(values)

    left = agree(lefts)
    right = agree(rights)
    top = agree(tops)
    bottom = agree(bottoms)
    if None in (left, right, top, bottom):
        return None
    full_w = width / (dpi / 25.4)
    full_h = height / (dpi / 25.4)
    if left + right > full_w * 0.28 or top + bottom > full_h * 0.28:
        return None
    if full_w - left - right < 8 or full_h - top - bottom < 8:
        return None
    confidence = 0.8 if len(votes) == 3 else 0.94
    return {
        "left": round(left, 2),
        "top": round(top, 2),
        "right": round(right, 2),
        "bottom": round(bottom, 2),
        "confidence": confidence,
    }


def inspect_raster_size(width_px: int, height_px: int, dpi: float | None, assumed: bool) -> tuple[float, float, float, bool]:
    use_dpi = dpi or 0
    dpi_assumed = assumed
    if use_dpi < 120:
        use_dpi = 300.0
        dpi_assumed = True
    width_mm = width_px / use_dpi * 25.4
    height_mm = height_px / use_dpi * 25.4
    return width_mm, height_mm, use_dpi, dpi_assumed


def read_psd_dpi(psd) -> float | None:
    try:
        from psd_tools.constants import Resource
        info = psd.image_resources.get_data(Resource.RESOLUTION_INFO)
        if not info or not info.horizontal:
            return None
        value = info.horizontal / 65536.0
        if info.horizontal_unit == 2:
            value *= 2.54
        if value < 1:
            return None
        return float(value)
    except Exception:
        return None


def inspect_pdf_page(page) -> dict:
    rect = page.rect
    width_mm = rect.width * PT_TO_MM
    height_mm = rect.height * PT_TO_MM
    trimbox = None
    warnings = []
    if page.rotation:
        warnings.append("صفحه PDF چرخش دارد؛ اندازه نمایشی استفاده شد.")
    else:
        trim = page.trimbox
        if trim and (rect.width - trim.width > 1.5 or rect.height - trim.height > 1.5):
            left = max(0.0, (trim.x0 - rect.x0) * PT_TO_MM)
            top = max(0.0, (trim.y0 - rect.y0) * PT_TO_MM)
            right = max(0.0, (rect.x1 - trim.x1) * PT_TO_MM)
            bottom = max(0.0, (rect.y1 - trim.y1) * PT_TO_MM)
            if left + right < width_mm - 5 and top + bottom < height_mm - 5:
                trimbox = {
                    "left": round(left, 3),
                    "top": round(top, 3),
                    "right": round(right, 3),
                    "bottom": round(bottom, 3),
                }
    return {
        "width_mm": width_mm,
        "height_mm": height_mm,
        "trimbox": trimbox,
        "warnings": warnings,
        "rotation": int(page.rotation or 0),
    }


SLUG_DPI = 288
_SLUG_CACHE: dict[tuple, bytes] = {}


def resource_root() -> str:
    """Fonts and assets. Beside the exe in the modular build, otherwise the bundle."""
    if getattr(sys, "frozen", False):
        beside = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.isdir(os.path.join(beside, "static", "fonts")):
            return beside
        return getattr(sys, "_MEIPASS", beside)
    return os.path.dirname(os.path.abspath(__file__))


def font_file(weight: str = "Regular") -> str:
    name = {
        "Regular": "Vazirmatn-Regular.ttf",
        "Medium": "Vazirmatn-Medium.ttf",
        "Bold": "Vazirmatn-Bold.ttf",
    }.get(weight, "Vazirmatn-Regular.ttf")
    return os.path.join(resource_root(), "static", "fonts", name)


def _render_slug_png(text: str, size_pt: float) -> bytes:
    """Draw the bindery line with the bundled font. No system Pango required."""
    size_pt = max(4.0, min(8.0, float(size_pt)))
    key = (text, round(size_pt, 2))
    hit = _SLUG_CACHE.get(key)
    if hit:
        return hit
    import pymupdf
    from PIL import Image

    font = font_file("Regular")
    if not os.path.exists(font):
        raise RuntimeError("فونت وزیرمتن کنار برنامه پیدا نشد.")
    width, height = 900, max(18, size_pt * 2.8)
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=width, height=height)
        css = (
            "@font-face {font-family: Vazir; src: url('%s');} "
            "p {font-family: Vazir; font-size: %.2fpt; direction: rtl; margin: 0; color: #111111;}"
        ) % (font.replace("\\", "/"), size_pt)
        page.insert_htmlbox(
            pymupdf.Rect(2, 1, width - 2, height - 1),
            "<p>%s</p>" % html.escape(text),
            css=css,
        )
        pix = page.get_pixmap(dpi=SLUG_DPI, alpha=True)
    finally:
        doc.close()
    image = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    bbox = image.getchannel("A").getbbox()
    if not bbox:
        raise RuntimeError("دستور صحافی خالی رسم شد")
    image = image.crop(bbox)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    _SLUG_CACHE[key] = data
    return data


def _draw_slug(page, layout: dict, page_index: int, page_count: int, errors: list[str]) -> None:
    import pymupdf

    slug = layout.get("slug") or {}
    if float(slug.get("w") or 0) < 1 or float(slug.get("h") or 0) < 1:
        return
    text = f"{slug['base']} · برگ {page_index + 1} از {page_count}"
    try:
        data = _render_slug_png(text, float(slug.get("size_pt") or 6.5))
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        rotate = int(slug.get("rotate") or 0) % 360
        if rotate:
            image = image.rotate(rotate, expand=True)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            data = buf.getvalue()
        im_w = image.width / SLUG_DPI * 25.4
        im_h = image.height / SLUG_DPI * 25.4
        scale = min(float(slug["w"]) / im_w, float(slug["h"]) / im_h, 1.0)
        draw_w = im_w * scale
        draw_h = im_h * scale
        x = float(slug["x"]) + (float(slug["w"]) - draw_w) / 2
        y = float(slug["y"]) + (float(slug["h"]) - draw_h) / 2
        rect = pymupdf.Rect(mm_to_pt(x), mm_to_pt(y), mm_to_pt(x + draw_w), mm_to_pt(y + draw_h))
        page.insert_image(rect, stream=data, keep_proportion=False)
    except Exception as exc:
        errors.append(f"دستور صحافی روی برگ چاپ نشد: {exc}")


def build_pdf(layout: dict, file_lookup: dict, dest_path: str, only_page: int | None = None) -> dict:
    """Place labels and crop marks into a print PDF.

    file_lookup maps file id -> {kind, path, raster_path, pdf_clip, width_mm, height_mm, trim}.
    pdf_clip is a pymupdf.Rect or None.
    """
    import pymupdf

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    doc = pymupdf.open()
    paper = layout["paper"]
    page_w = mm_to_pt(paper["width_mm"])
    page_h = mm_to_pt(paper["height_mm"])
    weight = float(layout.get("crop", {}).get("weight_pt") or 0.25)
    errors = []
    placed = 0
    image_xrefs: dict[str, int] = {}
    pdf_sources: dict[str, Any] = {}
    pages = layout["pages"]
    if only_page is not None:
        pages = [p for p in pages if p["index"] == only_page]
    try:
        for page_info in pages:
            page = doc.new_page(width=page_w, height=page_h)
            for cell in page_info["cells"]:
                if cell.get("empty"):
                    continue
                file_id = cell.get("file_id")
                spec = file_lookup.get(file_id)
                if not spec:
                    errors.append(f"فایل پیدا نشد: {cell.get('name') or file_id}")
                    continue
                frame = cell["content"] or cell
                target = pymupdf.Rect(
                    mm_to_pt(frame["x"]), mm_to_pt(frame["y"]),
                    mm_to_pt(frame["x"] + frame["w"]), mm_to_pt(frame["y"] + frame["h"]),
                )
                cell_rect = pymupdf.Rect(
                    mm_to_pt(cell["x"]), mm_to_pt(cell["y"]),
                    mm_to_pt(cell["x"] + cell["w"]), mm_to_pt(cell["y"] + cell["h"]),
                )
                try:
                    if spec["kind"] == "pdf":
                        src = pdf_sources.get(spec["path"])
                        if src is None:
                            src = pymupdf.open(spec["path"])
                            pdf_sources[spec["path"]] = src
                        clip = spec.get("pdf_clip")
                        page.show_pdf_page(target, src, spec.get("page_index") or 0, clip=clip)
                    else:
                        path = spec.get("raster_path") or spec["path"]
                        xref = image_xrefs.get(path)
                        if xref:
                            page.insert_image(target, xref=xref, keep_proportion=False)
                        else:
                            xref = page.insert_image(target, filename=path, keep_proportion=False)
                            image_xrefs[path] = xref
                    placed += 1
                except Exception as exc:
                    errors.append(f"{spec.get('name') or file_id}: {exc}")
                    page.draw_rect(cell_rect, color=(0.6, 0.2, 0.2), width=0.4)
            for mark in page_info.get("marks") or []:
                page.draw_line(
                    pymupdf.Point(mm_to_pt(mark["x1"]), mm_to_pt(mark["y1"])),
                    pymupdf.Point(mm_to_pt(mark["x2"]), mm_to_pt(mark["y2"])),
                    color=(0,),
                    width=weight,
                    lineCap=0,
                    lineJoin=0,
                )
            _draw_slug(page, layout, page_info["index"], layout.get("stats", {}).get("pages") or len(layout["pages"]), errors)
        doc.set_metadata({
            "title": "Label imposition",
            "creator": "Smart Label Placer",
            "producer": "Smart Label Placer",
        })
        if doc.page_count == 0:
            doc.new_page(width=page_w, height=page_h)
        doc.save(dest_path, garbage=4, deflate=True)
    finally:
        for src in pdf_sources.values():
            src.close()
        doc.close()
    return {"path": dest_path, "placed": placed, "errors": errors, "pages": len(pages)}
