# Active Context

**Дата фокуса:** 29.07.2026 (UTC+3)  
**Ветки дня (для push):** `29.07.26-AGENT-WORLD` · `29.07.26-PRD-BOT-ALL`  
**Hashes:** AW `af80fa4` · PRD `6977db7` (trailing after BE; liquid-pairs harden — локально, ещё не push)

## Текущий фокус

1. **Разбор убытка LONG EULUSDT (−44 USDT)** утро 29.07
   - В локальных `liquid_pairs_20260729_08/09` сигнал был **SOLUSDT SHORT**, EUL **не** в топ-15
   - Утром EUL был дикий памп 1.55→1.93 затем откаты — риск альта, не баг ордера бота из hourly
   - Ужесточён picker: BTC/ETH контекст, запрет LONG на dump-bounce, жёстче порог альтам, дисклеймер TG
   - Код готов локально на `29.07.26-AGENT-WORLD` — **commit/push по просьбе**
2. **Trailing after BE** — AW ON / prod OFF (уже в hashes выше)
3. Wallet Tracker / liquid pairs / SPIKE / polling — **не отключать**

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
