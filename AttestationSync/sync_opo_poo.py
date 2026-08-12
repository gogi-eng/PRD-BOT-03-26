# -*- coding: utf-8 -*-
"""
Рассылка файла «Текущая информация по состоянию ОПО и ПОО.xlsx»
в пять рабочих папок после редактирования.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sync_core import SyncJob, run_main

JOB = SyncJob(
    log_prefix="OPO_POO",
    title_ok="Рассылка ОПО и ПОО — готово",
    title_err="Рассылка ОПО и ПОО — есть ошибки",
    title_fail="Рассылка ОПО и ПОО — ошибка",
    shortcut_name_part="текущая информация по состоянию опо и поо",
    default_sources=(
        Path(r"\\srv-data\Doc\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность\Текущая информация по состоянию ОПО и ПОО.xlsx"),
        Path(r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность\Текущая информация по состоянию ОПО и ПОО.xlsx"),
    ),
    source_not_found_hint=(
        "Не найден файл «Текущая информация по состоянию ОПО и ПОО.xlsx».\n"
        "Проверьте ярлык на Рабочем столе или файл в папке\n"
        "«Промышленная безопасность» на сервере."
    ),
)


def main() -> int:
    return run_main(JOB)


if __name__ == "__main__":
    sys.exit(main())
