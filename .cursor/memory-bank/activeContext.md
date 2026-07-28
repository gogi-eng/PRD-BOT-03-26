# Active Context

**Дата фокуса:** 28.07.2026 (UTC+3)  
**Ветки дня (для push):** `28.07.26-AGENT-WORLD` · `28.07.26-PRD-BOT-ALL`  
**База tip:** AW от лайта 28.07; PRD от `27.07.26-PRD-BOT-ALL` + cherry-pick wallet_tracker

## Чат заархивирован

- **28.07.2026:** transcript `6aae46dd-a66c-41b2-816d-e8b0d328b395` → саммари (без сырого JSONL):
  - `docs/chat_archive/28.07.26-chat-wallet-tracker-liquid-pairs.md`
  - `docs/chat_archive/sessions/6aae46dd-a66c-41b2-816d-e8b0d328b395/28.07.26-chat-wallet-tracker-liquid-pairs.md`
- Темы: Wallet Tracker, liquid pairs, opposite hold, watches, API в `.env`.

## Текущий фокус

1. **Hourly liquid pairs → Telegram** — каждый час на ПК (Task Scheduler):
   - `scripts/hourly_liquid_pairs_report.py --telegram`
   - В MD/JSON блок `## Сигнал` или `## Почему без сигнала`
   - В TG: условный LONG/SHORT (вход/SL/TP) **или** простое обоснование «почему нет»
   - Credentials: `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` в `.env` (не в git); без них — `telegram skip: no credentials`
2. **Wallet Tracker v1 (advisory)** — AW ON / prod OFF; watches 9 ETH; без ордеров
3. ЛАЙТ SPIKE knobs AW: volume 1.40 / pullback 0.18 / cooldown 2400
4. Opposite hold / HTF / derivatives / SPIKE loops / polling — **не отключать**

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
