# -*- coding: utf-8 -*-
"""
Рассылка файла «АТТЕСТАЦИЯ ПО ПРОМЫШЛЕННОЙ БЕЗОПАСНОСТИ МКТС.xlsm»
в пять рабочих папок после редактирования.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sync_core import SyncJob, run_main

JOB = SyncJob(
    log_prefix="ATTEST",
    title_ok="Рассылка аттестации — готово",
    title_err="Рассылка аттестации — есть ошибки",
    title_fail="Рассылка аттестации — ошибка",
    shortcut_name_part="аттестация по промышленной безопасности мктс",
    default_sources=(
        Path(r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность\АТТЕСТАЦИЯ ПО ПРОМЫШЛЕННОЙ БЕЗОПАСНОСТИ МКТС.xlsm"),
        Path(r"\\srv-data\Doc\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность\АТТЕСТАЦИЯ ПО ПРОМЫШЛЕННОЙ БЕЗОПАСНОСТИ МКТС.xlsm"),
    ),
    source_not_found_hint=(
        "Не найден исходный файл аттестации.\n"
        "Проверьте ярлык на Рабочем столе или файл в папке\n"
        "«Промышленная безопасность»."
    ),
)


def main() -> int:
    return run_main(JOB)


if __name__ == "__main__":
    sys.exit(main())
