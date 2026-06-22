"""
Устаревший пакет bot — код перенесён в legacy/bot/.

Продакшен: python run_unified.py
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings
from pathlib import Path

_LEGACY_BOT = Path(__file__).resolve().parent.parent / "legacy" / "bot"
_DEPRECATION_SHOWN = False


def _warn_legacy() -> None:
    global _DEPRECATION_SHOWN
    if _DEPRECATION_SHOWN:
        return
    _DEPRECATION_SHOWN = True
    warnings.warn(
        "Пакет bot устарел (legacy/bot). Продакшен: python run_unified.py",
        DeprecationWarning,
        stacklevel=3,
    )


class _LegacyBotFinder(importlib.abc.MetaPathFinder):
    """Перенаправляет import bot.* → legacy/bot/*."""

    def find_spec(self, fullname, path, target=None):
        if fullname != "bot" and not fullname.startswith("bot."):
            return None
        rel = fullname.removeprefix("bot.").replace(".", "/")
        if rel:
            mod_file = _LEGACY_BOT / f"{rel}.py"
            if mod_file.is_file():
                _warn_legacy()
                return importlib.util.spec_from_file_location(
                    fullname,
                    mod_file,
                    loader=importlib.machinery.SourceFileLoader(fullname, str(mod_file)),
                )
            pkg_init = _LEGACY_BOT / rel / "__init__.py"
            if pkg_init.is_file():
                _warn_legacy()
                return importlib.util.spec_from_file_location(
                    fullname,
                    pkg_init,
                    submodule_search_locations=[str(pkg_init.parent)],
                )
            return None
        init = _LEGACY_BOT / "__init__.py"
        if not init.is_file():
            return None
        _warn_legacy()
        return importlib.util.spec_from_file_location(
            "bot",
            init,
            submodule_search_locations=[str(_LEGACY_BOT)],
        )


if not any(type(f).__name__ == "_LegacyBotFinder" for f in sys.meta_path):
    sys.meta_path.insert(0, _LegacyBotFinder())
