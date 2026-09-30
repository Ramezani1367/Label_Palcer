"""Thin entry for the Windows build.

The exe only starts the program. Python modules next to it, in modules/,
are loaded first so engine.py and app.py can be replaced without a rebuild.
"""

from __future__ import annotations

import sys
from pathlib import Path


def prefer_external_modules() -> None:
    if not getattr(sys, "frozen", False):
        return
    modules = Path(sys.executable).resolve().parent / "modules"
    if modules.is_dir():
        sys.path.insert(0, str(modules))


prefer_external_modules()

import app  # noqa: E402

app.main()
