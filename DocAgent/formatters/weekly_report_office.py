# -*- coding: utf-8 -*-
"""Еженедельный итог: оформление по Инструкции 2025, не через ДИ/папку Агент."""

from __future__ import annotations

import sys
from pathlib import Path

ATTESTATION = Path(r"C:\Users\v.dubovik\AttestationSync")
if str(ATTESTATION) not in sys.path:
    sys.path.insert(0, str(ATTESTATION))

from format_weekly_report import process_weekly_itog_document

__all__ = ["process_weekly_itog_document"]
