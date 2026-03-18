# Training + Model Integration (SMC v6)

## 1) Подготовьте `training_data.json`

Если файла нет — сначала прогоните бэктестер и соберите трейды в JSON.

## 2) Обучите модель

Из директории бота:

```bash
cd /root/PRD-BOT
python train_transformer.py --data training_data.json --epochs 220 --batch-size 32 --lr 0.002
```

Ожидаемый результат: появится файл `transformer_weights.pt`.

## 3) Убедитесь, что конфиг включает интеграцию

В `config.yaml` должны быть:

```yaml
entry:
  entry_threshold: 0.85
  trained_model_enabled: true
  trained_model_min_prob: 0.55
  trained_model_blend: 0.35
  trained_model_weights_path: "transformer_weights.pt"

bot:
  signal_cooldown_sec: 3600
```

## 4) Перезапустите бота

```bash
python main.py
```

В логах должны появиться строки:

- `Entry threshold=0.85 | same-side cooldown=3600s`
- `Trained model gate: ON (min_prob=0.55, blend=0.35)`

Если веса не найдены, увидите:

- `Trained model gate: OFF (checkpoint missing or disabled)`

## 5) Что теперь делает бот

- Жёсткий порог входа: `0.85`.
- Cooldown 1 час только для **same-side** сигнала по символу.
- Если `transformer_weights.pt` загружен:
  - бот считает `trained_model_prob`;
  - отбрасывает вход при `trained_model_prob < trained_model_min_prob`;
  - финальную `confidence` считает как blend:
    - `composite*(1-blend) + trained_prob*blend`.

## 6) Быстрый тюнинг

- Слишком мало сигналов: уменьшить `trained_model_min_prob` до `0.50`.
- Слишком много сигналов: поднять `trained_model_min_prob` до `0.60-0.65`.
- Сильнее влияние ML: поднять `trained_model_blend` до `0.45`.