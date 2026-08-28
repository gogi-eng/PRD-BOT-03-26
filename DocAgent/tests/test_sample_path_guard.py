# -*- coding: utf-8 -*-
"""Образец оформления — только папка Агент и слово «образец» в имени."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from path_resolver import (
    USER_AGENT_DIR,
    USER_PROJECT_DIR,
    assert_path_writable,
    is_allowed_sample_path,
    is_path_in_user_agent_dir,
    is_path_in_writable_user_dir,
    list_agent_sample_paths,
    live_user_agent_dir,
    pick_best_agent_sample,
    resolve_etalon_path,
)


def test_obmen_sample_rejected():
    path = Path(
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН"
        r"\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ПРОЕКТ_образец.docx"
    )
    assert is_allowed_sample_path(path) is False


def test_projects_writable_but_not_sample():
    path = USER_PROJECT_DIR / "ИНСТРУКЦИЯ по эксплуатации ЦТП.docx"
    assert is_path_in_writable_user_dir(path) is True
    assert is_path_in_user_agent_dir(path) is False
    assert is_allowed_sample_path(USER_PROJECT_DIR / "что-то_образец.docx") is False
    if USER_PROJECT_DIR.is_dir():
        assert_path_writable(path)


def test_agent_obrazec_allowed():
    path = USER_AGENT_DIR / "ПРОЕКТ Старший мастер_образец.docx"
    assert is_allowed_sample_path(path) is True


def test_agent_oformlen_rejected():
    path = USER_AGENT_DIR / "ПРОЕКТ Старший мастер_оформлен.docx"
    assert is_allowed_sample_path(path) is False


def test_copy_same_file_does_not_raise(tmp_path: Path):
    from path_resolver import copy_file_if_different, paths_are_same_file

    f = tmp_path / "a.docx"
    f.write_bytes(b"xx")
    assert paths_are_same_file(str(f).replace("\\", "/"), str(f)) is True
    copy_file_if_different(str(f).replace("\\", "/"), f)


def test_resolve_etalon_ignores_obmen_explicit():
    obmen = (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН"
        r"\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\foo_образец.docx"
    )
    found, _reason = resolve_etalon_path(explicit=obmen)
    if found is not None:
        assert is_allowed_sample_path(found) is True
        assert "обмен" not in str(found).casefold()


def test_coerce_allowed_sample_rejects_obmen_and_network():
    from agent_core import _coerce_allowed_sample

    obmen = (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН"
        r"\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ПРОЕКТ_образец.docx"
    )
    assert _coerce_allowed_sample(obmen) is None
    assert _coerce_allowed_sample(r"\\srv-data\doc\foo_образец.docx") is None
    assert _coerce_allowed_sample("") is None


def test_process_document_has_auto_pick_example_flag():
    import inspect
    from agent_core import process_document

    params = inspect.signature(process_document).parameters
    assert "auto_pick_example" in params
    assert params["auto_pick_example"].default is True


def test_choose_best_example_no_sample_returns_none(monkeypatch, tmp_path):
    import agent_core
    import path_resolver
    from agent_core import choose_best_example

    monkeypatch.setattr(path_resolver, "USER_AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent_core, "USER_AGENT_DIR", tmp_path)
    target = tmp_path / "ДИ инженер ЛСиМ_оформлен.docx"
    target.write_bytes(b"PK")
    found = choose_best_example("dolzhnostnaya_instrukciya", str(target))
    if found is not None:
        assert is_allowed_sample_path(found["path"]) is True
        assert "обмен" not in found["path"].casefold()
    else:
        assert found is None


def test_list_agent_samples_mock_obrazec_only(monkeypatch, tmp_path):
    """Мок-папка: в список попадает только *образец*.docx; без метки и ОБМЕН — нет."""
    import agent_core
    import path_resolver
    from agent_core import find_examples
    from path_resolver import list_agent_sample_paths, pick_best_agent_sample

    sample = tmp_path / "ДИ_тест_образец.docx"
    sample.write_bytes(b"PK")
    other = tmp_path / "без_метки.docx"
    other.write_bytes(b"PK")
    oformlen = tmp_path / "ПРОЕКТ_оформлен.docx"
    oformlen.write_bytes(b"PK")
    obmen_dir = tmp_path / "!!!ОБМЕН"
    obmen_dir.mkdir()
    (obmen_dir / "чужой_образец.docx").write_bytes(b"PK")

    monkeypatch.setattr(path_resolver, "USER_AGENT_DIR", tmp_path)
    monkeypatch.setattr(agent_core, "USER_AGENT_DIR", tmp_path)

    names = {p.name for p in list_agent_sample_paths()}
    assert names == {"ДИ_тест_образец.docx"}

    items = find_examples("dolzhnostnaya_instrukciya", limit=100)
    item_names = {i["name"] for i in items}
    assert "ДИ_тест_образец.docx" in item_names
    assert "без_метки.docx" not in item_names
    assert "чужой_образец.docx" not in item_names
    assert all("обмен" not in i["path"].casefold() for i in items)

    obmen_outside = Path(
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН"
        r"\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ПРОЕКТ_образец.docx"
    )
    assert is_allowed_sample_path(obmen_outside) is False

    target = tmp_path / "совсем_другое_оформлен.docx"
    target.write_bytes(b"PK")
    picked = pick_best_agent_sample(target)
    assert picked is not None
    assert picked.name == "ДИ_тест_образец.docx"


def test_list_empty_when_agent_dir_missing(monkeypatch, tmp_path):
    import path_resolver
    from path_resolver import list_agent_sample_paths, live_user_agent_dir

    missing = tmp_path / "нет_такой_папки"
    monkeypatch.setattr(path_resolver, "USER_AGENT_DIR", missing)
    assert live_user_agent_dir() is None
    assert list_agent_sample_paths() == []


def test_live_agent_dir_lists_obrazec_docx():
    from path_resolver import list_agent_sample_paths, live_user_agent_dir

    live = live_user_agent_dir()
    assert live is not None, "папка Агент должна быть доступна по N: или UNC"
    names = [p.name for p in list_agent_sample_paths()]
    assert names, "в папке Агент должны быть *образец*.docx"
    assert all("образец" in n.casefold() for n in names)
    assert all(n.lower().endswith(".docx") for n in names)
    assert all("обмен" not in str(p).casefold() for p in list_agent_sample_paths())
