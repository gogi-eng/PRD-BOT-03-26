"""Опциональное OCR для скриншотов сигналов (биржевые алерты в фото)."""
from __future__ import annotations

import io
import logging
from typing import Any

LOG = logging.getLogger("TG_AGENT")


def ocr_image_bytes(data: bytes, *, lang: str = "eng") -> str:
    """Распознаёт текст с изображения. Нужны: pillow, pytesseract и бинарник tesseract на сервере."""
    if not data:
        return ""
    try:
        from PIL import Image
        import pytesseract
    except Exception as exc:  # pragma: no cover
        LOG.debug("OCR: нет зависимостей pillow/pytesseract: %s", exc)
        return ""
    try:
        img = Image.open(io.BytesIO(data))
        return str(pytesseract.image_to_string(img, lang=lang) or "").strip()
    except Exception as exc:  # pragma: no cover
        LOG.debug("OCR: сбой распознавания: %s", exc)
        return ""


async def telethon_photo_ocr_text(client: Any, message: Any, *, max_bytes: int = 4_194_304) -> str:
    """Скачивает фото из Telethon-сообщения и возвращает распознанный текст (пусто если нет фото)."""
    if message is None or not getattr(message, "photo", None):
        return ""
    try:
        buf = await client.download_media(message, file=bytes)
    except Exception as exc:
        LOG.warning("OCR: не удалось скачать фото: %s", exc)
        return ""
    if not isinstance(buf, (bytes, bytearray)):
        return ""
    blob = bytes(buf)
    if len(blob) > max(1024, int(max_bytes or 0)):
        LOG.info("OCR: файл слишком большой (%s > %s байт), пропуск", len(blob), max_bytes)
        return ""
    text = ocr_image_bytes(blob, lang="eng")
    if len(text.strip()) < 12:
        text_ru = ocr_image_bytes(blob, lang="eng+rus")
        if len(text_ru.strip()) > len(text.strip()):
            text = text_ru
    return text.strip()
