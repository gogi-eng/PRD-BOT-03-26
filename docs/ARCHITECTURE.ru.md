# Архитектура PRD Unified Agent

```mermaid
flowchart TB
    subgraph inputs [Источники сигналов]
        OA[Собственные агенты\nmulti_agent_manager]
        TG[Telegram каналы\nPRD signal_agent]
        TV[TradingView webhook\nопционально]
    end

    subgraph core [prd-unified-agent]
        SR[SignalRouter]
        RG[RiskGuard]
        OR[UnifiedOrchestrator]
        TM[TradeMonitor]
        RP[BiHourlyReporter]
        SI[SelfImprover]
        CB[ControlBot кнопки]
    end

    subgraph external [PRD-BOT-03-26 clone]
        BC[exchange/bybit_client]
        EE[engine/entry_engine]
        TA[telegram_agent/*]
    end

    BYBIT[(Bybit USDT Perp)]
    CHAN[Telegram канал отчётов]

    OA --> SR
    TG --> TA --> SR
    SR --> OR
    OR --> RG
    RG -->|can_trade| OR
    OR --> BC --> BYBIT
    OR --> TM
    TM --> RP --> CHAN
    OR --> SI
    SI -->|low risk| CFG[config.yaml]
    CB --> OR
    CB --> SI
```

## Поток одного цикла (60 сек)

1. Читать открытые позиции с Bybit.
2. Сгенерировать собственные сигналы (если подключён PRD-repo).
3. Для каждого сигнала: `RiskGuard.can_trade()` → расчёт qty → `place_order`.
4. Каждые 2 часа: сверка PnL, формирование отчёта, предложения self-improve, отправка в канал.

## Разделение ответственности

| Слой | Отвечает за |
|------|-------------|
| PRD clone | Тяжёлая торговая логика, парсинг Telegram, бэктест |
| Unified agent | Оркестрация, единый риск-контур, отчёты, безопасный tune |
| Пользователь | Ключи API, лимиты в config, emergency stop |
