# -*- coding: utf-8 -*-
"""Ежедневный отчёт СНиОТ на правку в Word. Обёртка над weekly_report.py --daily."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import weekly_report as wr  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--daily" not in args:
        args = ["--daily", *args]
    sys.argv = [sys.argv[0], *args]
    return wr.main()


if __name__ == "__main__":
    raise SystemExit(main())
