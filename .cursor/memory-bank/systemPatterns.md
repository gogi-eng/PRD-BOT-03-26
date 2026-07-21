# Паттерны системы (кратко)

Обновлено: 2026-07-21

## Входы

- Orchestrator = полный pipeline  
- SPIKE/scanner = короткий путь → фильтры входа обязаны быть на **обоих**

## Деплой

- Прод: FETCH_HEAD + `install_production_config.sh` + restart trading_bot + telegram_signal_agent  
- AW: `deploy_agent_world_algo.sh`  
- Общий fix → обе дневные ветки

## Память агента

- Читать `PRD-BOT-ALL/.cursor/memory-bank/` в каждой сессии  
- Писать туда итоги значимых правок автоматически  
- Синхрон копий: `.vscode` и `AGENT-WORLD` `.cursor/memory-bank/`

Подробности: `prd-bot-workflow.mdc`, `no-encoding-spike-config-regressions.mdc`.
