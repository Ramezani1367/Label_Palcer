"""Lay out the modular Windows folder after PyInstaller.

Result:

    dist/LabelPlacer/LabelPlacer.exe
    dist/LabelPlacer/modules/app.py
    dist/LabelPlacer/modules/engine.py
    dist/LabelPlacer/static/
    dist/LabelPlacer/templates/
    dist/LabelPlacer/assets/
    dist/LabelPlacer/_internal/   libraries; do not edit
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "LabelPlacer"
NOTE = """ساختار ماژولار چیدمان هوشمند لیبل

LabelPlacer.exe را اجرا کنید و پنجره سیاه را نبندید.

این پوشه‌ها را می‌توانید بدون ساخت دوباره عوض کنید:
  modules/engine.py     منطق چیدمان، کاغذ، کراپ‌مارک و دستور صحافی
  modules/app.py        مسیرهای برنامه
  static/               رابط، استایل، فونت و آیکون مرورگر
  templates/            صفحه اصلی
  assets/               آیکون فایل اجرایی

پوشه _internal کتابخانه‌های پایتون و چاپ است. آن را جابه‌جا یا پاک نکنید.
کارهای ذخیره‌شده در LabelPlacer-data کنار همین فایل اجرایی می‌ماند.
"""


def _bundle_dir(dist: Path) -> Path:
    internal = dist / "_internal"
    if internal.is_dir():
        return internal
    return dist


def _copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def main() -> None:
    if not DIST.is_dir():
        raise SystemExit(f"اول PyInstaller را اجرا کنید. پوشه پیدا نشد: {DIST}")
    bundle = _bundle_dir(DIST)
    for name in ("static", "templates", "assets"):
        src = bundle / name
        if not src.exists():
            src = ROOT / name
        if not src.exists():
            raise SystemExit(f"پوشه {name} برای چیدمان ماژولار پیدا نشد.")
        _copy_tree(src, DIST / name)
    modules = DIST / "modules"
    if modules.exists():
        shutil.rmtree(modules)
    modules.mkdir()
    for filename in ("app.py", "engine.py"):
        shutil.copy2(ROOT / filename, modules / filename)
    (DIST / "MODULES.txt").write_text(NOTE, encoding="utf-8")
    print(f"modular: {DIST}")


if __name__ == "__main__":
    main()
