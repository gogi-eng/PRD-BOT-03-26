# -*- coding: utf-8 -*-
"""Обратная совместимость: делегирует в fix_sniot_document (все документы СНиОТ)."""

from fix_sniot_document import (  # noqa: F401
    EXIT_FILE_LOCKED,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_VALIDATION_FAIL,
    RULES,
    autofix,
    main,
    process_document,
    process_sniot_document,
    validate_di_document,
    validate_sniot_document,
)

if __name__ == "__main__":
    main()
