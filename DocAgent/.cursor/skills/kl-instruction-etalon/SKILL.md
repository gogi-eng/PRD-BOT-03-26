---
name: kl-instruction-etalon
description: >-
  Эталон Word из инструкции КЛ 0,4-10кВ 31.07.2026: поля, Normal/Heading1/BodyText,
  отступ 1,25 см (709 twips), таблицы 12 пт, главы по центру+caps.
---

# Эталон КЛ 31.07.2026

Файл-истина:  
`…\Дубовик В.В\ИНСТРУКЦИЯ по эксплуатации силовых КЛ  0,4-10кВ  31.07.2026--- ПОЛОЖЕНИЕ.docx`  
Копия: `DocAgent\etalons\Инструкция_по_эксплуатации_силовых_КЛ_0.4-10кВ_31.07.2026.docx`

## Что применять в коде

1. `ensure_etalon_styles(doc)` — Normal / Heading 1 / Body Text  
2. `apply_chapter_heading_format` — главы  
3. `apply_body_first_indents` — 709 twips  
4. `apply_table_fonts` — 12 пт  
5. `separate_contents_onto_own_page` — содержание  
6. `_wrap_job_title_for_signature` — длинные должности  

Модуль: `formatters/etalon_format_spec.py`.  
Правило: `.cursor/rules/kl-instruction-etalon.mdc`.
