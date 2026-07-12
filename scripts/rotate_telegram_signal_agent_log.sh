#!/usr/bin/env bash
# Ротация telegram_signal_agent.log (архив + сжатие + перезапуск агента).
#
# Зачем: FileHandler в scripts/telegram_signal_agent.py пишет в файл без лимита;
# на проде лог может вырасти до гигабайт и забить диск.
#
# Запуск на сервере (прод):
#   cd /root/PRD-BOT-ALL
#   sudo bash scripts/rotate_telegram_signal_agent_log.sh
#
# Песочница:
#   sudo bash scripts/rotate_telegram_signal_agent_log.sh /root/AGENT-WORLD
#
# Только посмотреть размер (без изменений):
#   bash scripts/rotate_telegram_signal_agent_log.sh --dry-run
#
# Раз в неделю через cron (пример, только если файл > 200 МБ):
#   0 4 * * 0 cd /root/PRD-BOT-ALL && bash scripts/rotate_telegram_signal_agent_log.sh --min-mb 200 >> /root/log_rotate_tg_agent.log 2>&1
set -euo pipefail

REPO=""
SERVICE=""
DRY_RUN=0
MIN_MB=50
KEEP=3
NO_GZIP=0
FORCE=0

usage() {
  cat <<'EOF'
Использование:
  rotate_telegram_signal_agent_log.sh [REPO_DIR] [опции]

Аргументы:
  REPO_DIR          Каталог бота (по умолчанию: каталог над scripts/)

Опции:
  --dry-run         Только показать размер и план, ничего не менять
  --min-mb N        Ротировать только если файл >= N МБ (по умолчанию 50)
  --keep N          Сколько архивов .gz оставить (по умолчанию 3)
  --service NAME    systemd-сервис (авто: telegram_signal_agent / telegram_signal_agent_world)
  --no-gzip         Не сжимать архив (оставить .log.YYYYMMDD_HHMMSS)
  --force           Ротировать даже если файл меньше --min-mb
  -h, --help        Эта справка
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --min-mb) MIN_MB="${2:?--min-mb требует число}"; shift ;;
    --keep) KEEP="${2:?--keep требует число}"; shift ;;
    --service) SERVICE="${2:?--service требует имя}"; shift ;;
    --no-gzip) NO_GZIP=1 ;;
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Неизвестная опция: $1" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -z "$REPO" ]]; then
        REPO="$1"
      else
        echo "Лишний аргумент: $1" >&2
        exit 2
      fi
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
REPO="$(cd "$REPO" && pwd)"
LOG_NAME="telegram_signal_agent.log"
LOG_PATH="${REPO}/${LOG_NAME}"
ARCHIVE_DIR="${REPO}/logs/archive"

if [[ -z "$SERVICE" ]]; then
  case "$(basename "$REPO")" in
    AGENT-WORLD) SERVICE="telegram_signal_agent_world" ;;
    *) SERVICE="telegram_signal_agent" ;;
  esac
fi

human_size() {
  local bytes="$1"
  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec-i --suffix=B "$bytes" 2>/dev/null || echo "${bytes} B"
  else
    echo "${bytes} B"
  fi
}

file_size_bytes() {
  if [[ -f "$1" ]]; then
    stat -c '%s' "$1" 2>/dev/null || stat -f '%z' "$1"
  else
    echo 0
  fi
}

BYTES=0
if [[ -f "$LOG_PATH" ]]; then
  BYTES="$(file_size_bytes "$LOG_PATH")"
fi
MB=$(( (BYTES + 1048575) / 1048576 ))
THRESHOLD_BYTES=$(( MIN_MB * 1048576 ))

echo "=== Ротация ${LOG_NAME} ==="
echo "Каталог:     ${REPO}"
echo "Файл:        ${LOG_PATH}"
echo "Размер:      $(human_size "$BYTES") (${MB} МБ)"
echo "Порог:       ${MIN_MB} МБ (ротация при >= порога; --force игнорирует порог)"
echo "Сервис:      ${SERVICE}"
echo "Архивы:      ${ARCHIVE_DIR} (хранить ${KEEP} шт.)"

if [[ ! -f "$LOG_PATH" ]]; then
  echo "Файл отсутствует — ротация не нужна."
  exit 0
fi

if [[ "$FORCE" -eq 0 && "$BYTES" -lt "$THRESHOLD_BYTES" ]]; then
  echo "Файл меньше порога ${MIN_MB} МБ — пропуск (добавьте --force для принудительной ротации)."
  exit 0
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_PLAIN="${ARCHIVE_DIR}/${LOG_NAME}.${STAMP}"
ARCHIVE_GZ="${ARCHIVE_PLAIN}.gz"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] Остановить: systemctl stop ${SERVICE}"
  echo "[dry-run] Переместить: ${LOG_PATH} -> ${ARCHIVE_PLAIN}"
  if [[ "$NO_GZIP" -eq 0 ]]; then
    echo "[dry-run] Сжать: gzip ${ARCHIVE_PLAIN}"
  fi
  echo "[dry-run] Запустить: systemctl start ${SERVICE}"
  echo "[dry-run] Удалить старые архивы (оставить ${KEEP})"
  exit 0
fi

mkdir -p "$ARCHIVE_DIR"

echo "=== Остановка ${SERVICE} ==="
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop "$SERVICE"
  sleep 2
else
  echo "WARN: systemctl не найден — остановите ${SERVICE} вручную перед ротацией" >&2
fi

echo "=== Архивирование ==="
mv "$LOG_PATH" "$ARCHIVE_PLAIN"
: > "$LOG_PATH"
chown root:root "$LOG_PATH" 2>/dev/null || true
chmod 644 "$LOG_PATH" 2>/dev/null || true

if [[ "$NO_GZIP" -eq 0 ]]; then
  echo "Сжатие ${ARCHIVE_PLAIN} ..."
  gzip -9 "$ARCHIVE_PLAIN"
  FINAL_ARCHIVE="$ARCHIVE_GZ"
else
  FINAL_ARCHIVE="$ARCHIVE_PLAIN"
fi

echo "Архив: $(human_size "$(file_size_bytes "$FINAL_ARCHIVE")") -> ${FINAL_ARCHIVE}"

echo "=== Запуск ${SERVICE} ==="
if command -v systemctl >/dev/null 2>&1; then
  systemctl start "$SERVICE"
  sleep 2
  if systemctl is-active --quiet "$SERVICE"; then
    echo "${SERVICE}: active"
  else
    echo "ERROR: ${SERVICE} не запустился — проверьте journalctl -u ${SERVICE} -n 40" >&2
    exit 1
  fi
else
  echo "Запустите сервис вручную: systemctl start ${SERVICE}"
fi

echo "=== Очистка старых архивов (оставить ${KEEP}) ==="
OLD_ARCHIVES=()
while IFS= read -r line; do
  OLD_ARCHIVES+=("$line")
done < <(find "$ARCHIVE_DIR" -maxdepth 1 -type f -name "${LOG_NAME}.*" -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
if [[ "${#OLD_ARCHIVES[@]}" -eq 0 ]]; then
  OLD_ARCHIVES=($(ls -1t "${ARCHIVE_DIR}/${LOG_NAME}".* 2>/dev/null || true))
fi
COUNT="${#OLD_ARCHIVES[@]}"
if [[ "$COUNT" -gt "$KEEP" ]]; then
  for ((i = KEEP; i < COUNT; i++)); do
    rm -f "${OLD_ARCHIVES[$i]}"
    echo "Удалён: ${OLD_ARCHIVES[$i]}"
  done
else
  echo "Старых архивов для удаления нет (${COUNT} <= ${KEEP})."
fi

NEW_BYTES="$(file_size_bytes "$LOG_PATH")"
echo "=== Готово ==="
echo "Новый ${LOG_NAME}: $(human_size "$NEW_BYTES")"
echo "Проверка: tail -5 ${LOG_PATH}"
echo "Журнал:   journalctl -u ${SERVICE} -n 15 --no-pager"
