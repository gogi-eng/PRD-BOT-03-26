# Hermes — почасовой просмотр для агента Cursor

**Репозиторий:** https://github.com/gogi-eng/Analise_Hermes  
**Локальная копия:** `Analise_Hermes/` (git pull перед чтением)

## В начале сессии / по запросу Виктора

```bash
cd Analise_Hermes && git fetch origin main && git reset --hard origin/main
```

Читать по порядку:
1. `meta.json` — `fingerprint`, `updated_at`, `signal_maps_count`
2. `HERMES_LIVE.md` — ZeroOne гипотеза + топ рекомендаций
3. **`HERMES_SIGNAL_MAPS.md`** + **`hermes_signal_maps.jsonl`** — карта каждого сигнала (вход, вирт. исход, трейлинг)
4. `winning_entry_rules_report.md` — полный отчёт
5. При смене темы: `worst_trading_hours.md`, `bot_performance_comparison.md`, `AI_Learnings_and_Recommendations.md`

## Правила для агента

- Рекомендации Hermes — **только предложения**, не менять `config.yaml` без явного «да» от Виктора.
- **ZeroOne:** максимум одна правка config за цикл, 3–5 дней наблюдения.
- **Не ослаблять без согласования:** дневной лимит убытка, дубли BTC/ETH/SOL, panic/supervisor на лимитах.
- **Сверять с журналом:** виртуальный TP ≠ деньги; смотреть `trade_history.jsonl` (bot vs manual).
- Hermes «снять max_positions» — **обычно отклонять**, если WORLD переторговывает.

## На сервере (уже крутится)

`hermes-cursor-feed.service` на AGENT-WORLD пушит отчёты в GitHub ~каждые 2 ч.

**Карты сигналов (каждые 3 ч):** `hermes-signal-maps.timer` на PRD-BOT-ALL → `hermes_signal_maps.jsonl`

## Последний известный fingerprint

См. `Analise_Hermes/meta.json` после pull.
