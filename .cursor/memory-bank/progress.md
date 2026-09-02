## 02.09.2026 — регистрация ИП под услуги ОТ/ПБ (ot-pb.by)

- Чат: `.cursor/chats/archive/02.09.26-IP-OT-PB.html` (открывается в браузере)
- План: `.cursor/chats/archive/02.09.26-plan-registraciya-IP.html`
- Заявление (бланк Минюста, прил. 3): `.cursor/chats/archive/02.09.26-zayavlenie-IP.docx`
- Ветка: `02.09.26-ИП-ОТ-ПБ`
- Прайс документов/видео/аудита/автоматизации — **ИП**, не НПД
- Основной код заявления: **74909**; затем аккредитация Минтруда
- В Минске заявление несут в управление регистрации ГУЮ Мингорисполкома, не в Ленинский райисполком
- Аккредитация Минтруда: функции специалиста по ОТ (не аттестация РМ); Победителей 23/2 каб. 606; тел. 371-09-56; проверка знаний — комиссия Ленинского района; бесплатно, 5 раб. дней, 3 года
- Памятка: `.cursor/chats/archive/02.09.26-akkreditaciya-Mintrud.html`
- Лицензируемый промбез с 01.01.2026 — не ИП
- **Не сделано (дом):** дописать на ot-pb.by опыт ОТ + 6 лет руководства отделом; исходник на сервере; памятка `.cursor/chats/archive/02.09.26-ot-pb-opyt.html`

## 02.09.2026 — акты техрасследования: Хандуратов вместо Лешко

- Комиссия: Лукашевич Г.Л., Стародубцев С.Н., ведущий инженер СНиОТ Хандуратов М.Н., начальник РТС-4/5
- Убраны Лешко, Ванагель (АРС), Рафеенко (ЛСиМ)
- Подписанты — только члены комиссии; интервал 1,5; ФИО на 12 см
- Папка: `N:\…\Хандуратов\!!! От Дубовика\Акты_техрасследования_20-26.08.2026`
- Код: `DocAgent/formatters/damage_investigation_acts.py`; тесты 10 passed

## 09.08.2026 — hotfix 08.08 на оба инстанса (SPIKE P0/P1 prod ON)

- Код manual/Companion/SPIKE bypass уже был в tip `09.08.26-*` (после 08.08)
- Config: на **проде** включены `spike_bypass_no_corridor: true` и `spike_scalp.pullback_entry.enabled: true` (как на AW)
- Manual/Companion защита уже была в обоих deploy yaml (`manual_auto_close` / `auto_close_manual` = false)
- Тесты: manual + zone_corridor + spike_pullback + pullback_entry + sl_tp_guard — 35 passed
- SPIKE loop прод: `run_loop_in_signal_agent: true` сохранён

## 09.08.2026 — SL/TP guard на открытых позициях

- Модуль `prd_agent/positions/sl_tp_guard.py` + вызов в `PositionSteward.manage()` (до early-return trailing)
- Config `positions.sl_tp_guard` в production и agent_world_sandbox
- При пустых SL/TP на Bybit: WARNING `Missing SL/TP on position` → trading-stop restore → INFO `SL/TP guard`
- Manual тоже (include_manual), без time-stop/Companion close
- Тесты: `backend/tests/test_sl_tp_guard.py`
- Ветки: `09.08.26-PRD-BOT-ALL` + `09.08.26-AGENT-WORLD`

## 08.08.2026 — HOTFIX manual time-stop (SNDKUSDT)

- Причина: adopt manual наследовал opened_at из `_bot_levels` прошлой bot-позиции → мгновенный `close_time_stop`
- Фикс: `positions.manual_auto_close: false`, `trade_companion.auto_close_manual: false`, fresh opened_at for manual, clear `_bot_levels` on drop
- Тесты: `test_manual_position_no_instant_close.py` + companion
- Ветки: `08.08.26-AGENT-WORLD` + `08.08.26-PRD-BOT-ALL`

## 08.08.2026 — P0 SPIKE bypass no_corridor + P1 pullback (AW)

- P0 config: `trading.zone_corridor_play.spike_bypass_no_corridor` (+ min_score/min_move)
- P1: `market_scanner.spike_scalp.pullback_entry` — с 09.08 ON на **обоих** инстансах
- Branches: `08.08.26-AGENT-WORLD`, `08.08.26-PRD-BOT-ALL`

# Progress

## 31.08.2026 — агент «Проверка_знаний-Госпромнадзор»

- Код: `C:\Users\v.dubovik\Проверка_знаний-Госпромнадзор\`
- Ярлык на рабочем столе; планировщик 20/21/22 в 09:00 и опрос почты каждые 20 мин
- Отбор по правилам Excel (красный = просрочка, жёлтый = 30 дней) — проверка на живом xlsm: 11 человек (РТС-1/4/5/6/7, АРС, ЛСиМ)
- Рассылка: сначала открывает Excel (общий файл + файлы подразделений + образец заявки) и ждёт кнопку «Отправить письма»
- Почта: Roundcube в Google Chrome, не Outlook
- Бронь: Digial-Q `http://212.98.182.67:8300/view` через Chrome/Playwright
- После докладной: Word на проверку, 1С пользователь отправляет сам
- Тесты: `tests/test_agent.py`
- Skill/rule: `gospromnadzor-knowledge-check`

## 28.08.2026 — исходник и образец с любого пути

- Указание владельца: убрать запрет «только две папки».
- Чтение исходника/образца — любой указанный путь (Downloads, ОБМЕН, САТП, Агент, Проекты).
- Запись — поле «Результат»; предупреждение ОБМЕН/САТП, без блокировки. Пусто — рядом с исходником.
- Не сканировать весь `N:\`. SameFile `/`=`\` сохранён.
- Сборка `2026-08-28-any-path`. Тесты path guard + damage acts + body safety.

## 24.08.2026 — правка в Агент и Проекты

- Сборка `2026-08-24-agent-projects`.
- Тогда: делопроизводитель писал в **Агент** и **Проекты**; образец только Агент + «образец».
- С 28.08 это **снято** указанием владельца (см. блок выше).
- `assert_path_writable` / `is_path_in_writable_user_dir`; результат `_оформлен` рядом с исходником.
- Правила: `sniot-user-folder-only.mdc`, skill, `sniot-di-documents.mdc`. Тесты path guard — 17 passed.

## 19.08.2026 — титул 6–8 пустых перед грифом + пунктуация перечисления

- Сборка `2026-08-19-stamp6-enum-punct`.
- После шапки предприятия перед таблицей «название + УТВЕРЖДАЮ» — 6–8 пустых строк (цель 6).
- «МИНСК 2026» внизу титула; глава 1 с верха новой страницы без пустых строк.
- Перечень «должен знать»: промежуточные `;`, последний `.`; вставка «основы делопроизводства» как последний пункт — с точкой.
- Папку Агент не перезаписывали.

## 19.08.2026 — титул: одна пустая строка, не шесть

- Сборка `2026-08-19-title-restore`.
- Откат `TITLE_EMPTY_BEFORE_STAMP = 6`: между шапкой и грифом снова не больше одной пустой строки.
- Сохранено: без Tab во 2-м столбце; «МИНСК 2026» в теле 1-й стр.; разрыв раздела после Минска.
- Папку Агент не перезаписывали.

## 19.08.2026 — названия глав по центру (не «1.9. должен знать»)

- Сборка `2026-08-19-chapter-center`.
- «Заголовки разделов» у пользователя = названия глав `1 ОБЩИЕ ПОЛОЖЕНИЯ`, `2 ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ`.
- Ошибочное центрирование строк `1.9. … должен знать:` отменено: они снова тело с отступом 1,25 см.
- Папку Агент не перезаписывали.

## 19.08.2026 — пунктуация всего документа после правки

- Сборка `2026-08-19-punct-whole-doc`.
- После любой правки обязательно проверить знаки препинания **всего** docx (титул, тело, таблицы, подписанты).
- Код: `check_document_punctuation_after_edit` в конце `apply_mandatory_layout_fixes`; `validate_document_punctuation` в `validate_sniot_document`.
- Правило/skill: `sniot-di-documents.mdc`, `sniot-di-documents/SKILL.md`.
- Папку Агент не перезаписывали.

## 19.08.2026 — титул без таба, МИНСК в теле, разрыв после Минска, делопроизводство и запятые

- Сборка `2026-08-19-minsk-break-ch1`.
- Правый столбец грифа: **без табуляции** (линия + пробел + ФИО); дата без таба.
- «МИНСК YYYY» в теле 1-й страницы (`vAnchor=margin`); сразу после — `nextPage`; глава 1 с верха стр. 2.
- В перечень «должен знать:» добавить `основы делопроизводства;`, если нет.
- Оборот: `исполняет, по распоряжению …, специалист`.
- Папку Агент / ОБМЕН не перезаписывали.

## 17.08.2026 — поля 30/10/20/20 и ФИО на одной строке

- Сборка `2026-08-17-1431-margins-fio-tab`.
- Поля СНиОТ: левое 30 / правое **10** / верх 20 / низ 20 мм (Минюст №65 п.18 допускает правое ≥8 мм).
- Подписанты: должность+ФИО через Tab на одной строке; дата новой строкой с тем же табом, что начало ФИО.
- `process` / `apply_mandatory_layout_fixes` вызывают; `validate_page_margins` / `validate_signatory_date_plaques` ловят.
- Папку Агент не перезаписывали.

## 17.08.2026 — титул слева/справа + «номер инструкции» 12 pt

- Сборка `2026-08-17-1342-title-left-right`.
- `format_title_block` реально собирает таблицу 2 колонки: слева название, линия, «номер инструкции» 12 pt; справа УТВЕРЖДАЮ, должность, линия+ИОФ, дата keepLines.
- «МИНСК YYYY» в теле (`framePr`), вычищается из колонтитула.
- Подписанты: без лишних пустых строк (`compact_extra_empty_lines_in_signatory_block`).
- Путь ОБМЕН не записан как образец. Папку Агент не перезаписывали.
- pytest title + DocAgent `test_sniot_document.py` — green.

## 17.08.2026 — список образцов DocAgent из папки Агент

- Причина пустого списка: `USER_AGENT_DIR` с латинской «i» (СНiОТ), на диске кириллица «и»; `list_agent_sample_paths` сразу возвращал [].
- Живой каталог: N: или UNC `\\srv-data\doc\9 - …\Агент`. 6 файлов `*образец*.docx`.
- Сборка `2026-08-17-1335-agent-samples`. pytest `test_sample_path_guard.py` + `test_sniot_document.py` — 29 passed.

## 17.08.2026 — титул: константы TITLE_* из эталона (только чтение ОБМЕН)

- Сборка `2026-08-17-1136-title-etalon`
- Геометрию титула сняли один раз с файла в ОБМЕН; путь **не** записан как образец DocAgent
- `format_title_block` / `place_title_city_year_at_bottom` применяют: шапка центр; УТВЕРЖДАЮ справа не жирный; наименование слева; МИНСК YYYY внизу 1-й
- `_оформлен` не перезаписывали; pytest title — green

## 14.08.2026 — проверка русского всем, без аббревиатур

- Сборка `2026-08-14-1348-ru-all-no-abbrev`
- `russian_check` снова для ДИ/РИ/положений/инструкций/еженедельного итога; галочка GUI по умолчанию включена, conservative_di её не снимает
- Аббревиатуры (ЛСиМ, СНиОТ, ТКП…) спеллер/LT/локальные замены не трогают; text_edits и перенумерация исходника не возвращались
- `process()` по-прежнему не вызывает `apply_russian_phrase_rules`

## 14.08.2026 — ДИ: не менять слова, снять выделение, таблица→подписанты

- Сборка `2026-08-14-1307-no-text-edits-di`
- Для любой ДИ: text_edits / russian_check / канцелярские фразы / finalize выключены (галочка GUI по умолчанию снята и игнорируется)
- `process()` не вызывает `apply_russian_phrase_rules`; highlight/shd снимаются в конце и после Word COM
- Таблица согласования: последняя 2–3 колонки (шапка Ф.И.О.), не лист ознакомления; повторный разбор в конце process
- Диагностика ЛСиМ: спеллер ЛСиМ→Осим; ТКП+(02300); таблица Рафеенко… на месте, потому что СНиОТ не перезаписал `_оформлен`
- pytest 156 passed; файлы в Агент не перезаписывали

## 14.08.2026 — цепочка DocAgent: запись блокировалась, GUI показывал «Было»

- Лог 11:56: `SNIOT pass not applied`, код 1; черновик в AttestationSync, `_оформлен` не тронут
- Блокеры записи: ложный «Дубль титула» (шапка до главы 1) и w:tabs на маркерах «Разработал:»/«Согласовано:»
- В process() добавлен вызов `apply_signatory_tab_stops`; САТП-перенумерация только при `has_di_satp_numbering`
- DocAgent: ДИ без перенумерации в publish_check/finalize; в stdout только «Осталось»; сборка `2026-08-14-1205-process-calls-save`
- pytest: `test_sniot_body_safety.py` + DocAgent tests — 158 passed
- Не --apply; исходный `.doc` и `_оформлен` не перезаписывали

## 14.08.2026 — нумерацию исходника сохранить (не схема САТП)

- Не удалять номера из исходника (текст или ListString Word) — только починить ошибки и сверить набор
- Не подсвечивать чужие/исправленные номера (highlight/shading снимаются)
- Таблица должностей и фамилий → блок «Разработал:» / «Согласовано:» без таблицы
- Сборка `2026-08-14-1136-keep-source-numbers`; pytest `test_sniot_body_safety.py` 129 passed
- Не --apply на `_оформлен`; исходный `.doc` не трогали; конвертация .doc → TEMP

## 14.08.2026 — никаких маркеров списка (все агенты)

- В оформленных Word СНиОТ / делопроизводитель / еженедельный итог не оставлять •, , ○, ■, дефис-списки, bullet `numPr`
- Остаются нумерованные пункты (1.1., 2.2.1.) и обычный текст; номера страниц и Tab подписантов не трогать
- Код: `remove_list_markers_in_body`, `validate_list_markers` в `fix_sniot_document.py`; weekly и DocAgent — тот же запрет
- Сборка `2026-08-14-1035-no-list-markers`; pytest 130 passed
- Не --apply на пользовательские docx, ОБМЕН не трогали

## 14.08.2026 — делопроизводитель: один пробел, образец только Агент, интервал 1.0/0 pt

- Все документы делопроизводителя: не больше одного пробела подряд; nbsp→пробел; validate ловит остаток (титул тоже)
- Образец только папка Агент + слово «образец»; ОБМЕН/сеть/«Стандарт» не подтягивают чужой путь
- Общее правило агентов: межстрочный одинарный 1.0, space_before/after тела 0; подписанты 1,5
- Сборка `2026-08-14-1015-office-spaces-spacing`

## 14.08.2026 — еженедельный итог: делопроизводство 2025 + один пробел

- Оформление отчёта «Еженедельный итог» (рабочий стол, не папка Агент)
- `AttestationSync/format_weekly_report.py` — поля 30/8/20/20, TNR 14, отступ 1,25 см, одинарный интервал, номера стр. со 2-й, без двойных пробелов
- Вызов из `Desktop\Еженедельный_итог\weekly_report.py` после сборки отчёта; DocAgent вид `ezhenedelnyy_itog`
- Тесты: `test_format_weekly_report.py` + detect в DocAgent — 20 passed
- Пользовательские docx итога не перезаписывали пакетом

## Сделано (06.08.2026)

### Trailing tighten after BE −0.5%
- [x] `trailing_after_be`: вместо widen → `distance_reduce_pct: 0.5` (оба deploy yaml)
- [x] `apply_trailing_after_be_distance`: max(min_floor, base − 0.5)
- [x] Маркер: `Trailing tighten after BE −0.5%`
- [x] Тесты `test_trailing_after_be.py` + py_compile
- [ ] Hash/деплой — см. activeContext после push

## Сделано (04.08.2026)

### Daily-loss reset + Order OK
- [x] `RiskGuard`: флаг ручного сброса до конца торгового дня (`timezone_offset`), skip `reconcile_from_closed_rows`, маркер `MANUAL_DAILY_LOSS_RESET`
- [x] `orchestrator`: Order OK — передан `qty` (нет TypeError в journal)
- [x] Тест `backend/tests/test_reset_daily_loss.py`
- [ ] Hash/деплой — см. activeContext после push

## Сделано (02.08.2026)

### Ежедневный ритуал агента (после 18:00 UTC+3)
- [x] Раздел в плане `dumps/PRD_BOT_stack_program_2026-08-02.txt` (ритуал + изучение логов)
- [x] Rule `daily-stack-program.mdc` (`.vscode` + `PRD-BOT-ALL`) — alwaysApply
- [x] Hook `sessionStart`: `.cursor/hooks.json` + `hooks/daily_stack_ritual.py`
- [x] Маркер `dumps/.daily_ritual_last.txt`; approval gate на код/push/deploy
- [x] В ритуал встроено: SSH journalctl прод+AW → «По логам» + «Предлагаю изменить»
- [ ] Пользователь: каждый вечер смотреть Лабораторию / По дням (фаза 0–1)

### Внедрение оценки (без ослабления фильтров)
- [x] Live config снят с сервера (до): risk 0.225, lev min/max 10–15, Zone ON, GARCH ON, adopt_manual true, trailing_act 2.5, auto_apply true на **обоих**
- [x] Прод: `auto_apply_low_risk: false`; AW auto_apply оставлен true; rate-limit `max_auto_applies_per_hour: 1` в SelfImprover (+ оба deploy yaml)
- [x] «📅 По дням»: раздельно бот / ручные / итог; ключи `analytics.daily_pnl_split_origin`, `analytics.exclude_manual`
- [x] Тесты: `test_daily_pnl_and_lab_reports.py`, `test_self_improver_batch_reload.py`
- [x] Push обе ветки + деплой + grep live `auto_apply_low_risk: false` на проде
  - PRD `02.08.26-PRD-BOT-ALL` @ `2aa04e7` · AW `02.08.26-AGENT-WORLD` @ `f2d6d37`
  - Live: PRD `auto_apply_low_risk: false`, AW `true`; оба `max_auto_applies_per_hour: 1`, `daily_pnl_split_origin: true`
- [ ] Наблюдение 2–3 дня AW (Лаборатория) — см. activeContext

### Disk cleanup + keep last config bak
- [x] scripts/server_disk_cleanup.sh (dry-run / CONFIRM=1)
- [x] scripts/prune_config_backups.sh — keep newest bak
- [x] Встроено в install_production / install_agent_world / deploy_agent_world_algo
- [x] ПК: scripts/pc_bot_folders_cleanup.ps1

### Risk 0.225 + leverage min 10
- [x] `dynamic_leverage.min` 5→10 (prod + AW deploy yaml)
- [x] `risk_pct_per_trade: 0.225` зафиксирован; пресеты/min_risk/notional ×1.5 не откатывали
- [x] AW sandbox notional на ветке PRD: ошибочные 120→**45** (как live AW)
- [x] SelfImprover пол risk 0.1→**0.225** (причина live 0.1 после install)
- [x] `verify_live_sizing_config.py` в install_production / install_agent_world
- [x] Push: `02.08.26-PRD-BOT-ALL` @ `62f1946` · `02.08.26-AGENT-WORLD` @ `42c1740`

## Сделано (01.08.2026)

### Размер позиций ×1.5 (прод + AW)
- [x] `trading.risk_pct_per_trade` 0.15→0.225; пресеты 0.25/0.35/0.45→0.375/0.525/0.675
- [x] `supervisor_v4.min_risk_pct` 0.1→0.15; SPIKE notional: прод 80→120, AW 30→45
- [x] `signal_notional.py`: clamp pct до 200% (раньше 100% резал ×1.5)
- [x] Push: PRD `ba57f07` · AW `4f0cab6` (`01.08.26-*`)

## Сделано (30.07.2026)

### Доступ к DigitalOcean
- [x] Восстановлен SSH/FileZilla (IP `207.154.238.178`, ключ на ПК)
- [x] Гайд: `docs/server-access-ssh-filezilla.md`
- [x] Архив выжимки: `.cursor/chats/archive/30.07.26-ssh-filezilla-access.md`
- [x] Правило: при SSH/FileZilla → сначала гайд в репо
- [x] Push обе ветки `30.07.26-*`: PRD `2983891` · AW `235ac13`

## Сделано (26.07.2026)

### Длинный трейлинг (26.07)
- [x] Config: distance 3.5%, atr 2.2, min 1.8%, adaptive tight 0.85, late_tighten 1.0 / SPIKE 0.90
- [x] deploy prod + AW + live config.yaml (оба workspace)
- [x] Push `26.07.26-*` + деплой (по просьбе)
  - PRD trailing: `919b3a6` · AW: `4dac838`

### Opposite EXIT: не сносить SPIKE own-сигналом
- [x] `opposite_signal_policy.py` + wiring в orchestrator
- [x] Config `skip_spike_on_own_signal: true` (deploy prod + AW + live yaml)
- [x] Тесты 8 passed; маркер `Opposite signal EXIT skipped SPIKE`
- [x] Push обе ветки `26.07.26-*` + деплой (по просьбе)
  - PRD: `8e882ab` · AW: `a6948d3`

## Сделано (21–22.07.2026)

### Memory Bank
- [x] Канон `.cursor/memory-bank/` + `memory-bank.mdc` (alwaysApply)
- [x] Автообновление после значимой работы; UMB; push с дневными ветками
- [x] Сниппет User Rules: `PRD-BOT-ALL/.cursor/USER-RULES-SNIPPET.txt`
- [x] Копии в `.vscode` и `AGENT-WORLD`
- [x] Tip 21.07: PRD `7e5b64d` · AW `49e1473`

### GARCH sizing (22.07, код готов, push по просьбе)
- [x] `prd_agent/risk/volatility_regime_sizing.py` — GARCH(1,1) calm/normal/storm
- [x] Wiring: orchestrator + telegram_signal_agent `_execute`
- [x] Config: AW `enabled: true`, prod `enabled: false`
- [x] Тесты: `backend/tests/test_volatility_regime_sizing.py` (9 passed)
- [ ] Push обе ветки дня + деплой AW (install config + restart)

### Trade / Telegram
- [x] Trade Companion — AW ON, prod OFF (`00bc7ef` / `a17f388`)
- [x] Trade Lifecycle — статистика сделок (`79525be` / `a437238`)
- [x] Restore bybit_monitor после disable Hermes (`fa64398` / `ef37bd0`)
- [x] Правило целостности кода (не вырезать B при disable A)
- [x] AW journal: `TRADE COMPANION` + `TRADE LIFECYCLE` подтверждены
- [x] max_notional_balance_pct 80% (оба); AW own phase: notional 30% + own_agents ON
- [x] Spike на проде: `run_loop_in_signal_agent: true` + отдельный `.spike_scan.lock`
- [x] Trading hours: боты не stop; pre-block закрывает только убыточные; новые входы блоком часов

### Ранее (не откатывать)
- Hermes OFF, soft×0.55 AW, NY weekends, wallet harden, flat-PnL≠consecutive

## Дальше

1. Soak Companion + GARCH на AW 3–5 дней → решение по prod
2. Фаза 2 own+BOS на AW — по PnL own vs SPIKE
3. User Rules сниппет на всех ПК аккаунта (включая пункт про SSH-гайд)

## Не делать без явной просьбы

- Включать Hermes / Companion / GARCH на проде
- Удалять bybit_monitor при правках Hermes
- ESPORTS blacklist без просьбы
- Ослаблять daily loss / max_positions по виртуальным TP Hermes
- Класть в git пароли / private keys / `.env`

## 08.08.2026 — Companion BLESS hotfix
- Причина: trade_companion «разворот» SMA8/21 без flip (не trailing/BE/opposite).
- Правка: prior trend + hold 300s + пороги −3.5%/0.8%; тесты test_trade_companion.py.

