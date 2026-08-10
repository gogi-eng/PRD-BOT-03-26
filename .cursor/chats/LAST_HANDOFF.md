# LAST HANDOFF

- **дата:** 10.08.2026 (UTC+3)
- **чат:** [Chat_10_08_26](archive/Chat_10_08_26.md)
- **ветка:** `10.08.26-AGENT-WORLD`
- **тема:** дамп песочницы AGENT-WORLD; прямой DeepSeek API в `llm_gateway`

## Код

- Провайдер `deepseek` в `prd_agent/ai/llm_gateway.py` (OpenAI-compatible)
- `.env`: `DEEPSEEK_API_KEY=...`
- Конфиг: `ai.provider: deepseek` + секция `deepseek:`
- По умолчанию в yaml всё ещё `openrouter` (безопасный дефолт)

## Включение на сервере

```bash
# в /root/AGENT-WORLD/.env
DEEPSEEK_API_KEY=sk-...

# в config.yaml
ai:
  provider: deepseek
```

Затем: `sudo systemctl restart trading_bot_agent_world`
