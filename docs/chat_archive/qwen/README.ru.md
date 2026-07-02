# Чаты Qwen (внешний архив)

Экспорты из Qwen Chat, связанные с проектом PRD-BOT и монетизацией.

| Файл | Тема | Дата экспорта |
|------|------|---------------|
| `chat-export-1782824460301.json` | Заработок на дом → Беларусь → упаковка бота (Форматы A–D) | 29.06.2026 |

## Содержание чата `1782824460301`

1. **Вопрос:** чем заняться, чтобы заработать на дом.
2. **Уточнение:** живу в Беларуси, в РФ не работаю.
3. **Ответ «по порядку — 1»:** детальный план упаковки бота в продукт (Форматы A–D, $400, USDT, НПД).

Краткое резюме для ТЗ: `qwen-product-plan-summary.ru.md`

## Импорт нового экспорта

```powershell
cd C:\Users\Labuh\.vscode\PRD-BOT-ALL
scripts\import_qwen_chat.bat
```

Или укажите файл:

```powershell
powershell -File scripts\import_qwen_chat.ps1 -Source "C:\Users\Labuh\Downloads\chat-export-XXXX.json"
```

После импорта — push архива (если нужно в GitHub):

```powershell
powershell -File scripts\push_chat_archive.ps1 -UseTempClone
```

## Связанные документы

- ТЗ продукта: `docs/TZ_PRODUCT_FORMAT_A_29_06_26.md`
- Issues недели 1: `docs/GITHUB_ISSUES_WEEK1_PRODUCT.md`
