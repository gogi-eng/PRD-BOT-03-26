#!/usr/bin/env bash
# Out-of-process transformer retrain (pair with config feedback_loop.retrain_in_process: false)
set -euo pipefail
REPO_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "${REPO_DIR}"
if [[ -x "${REPO_DIR}/venv/bin/python" ]]; then
  PY="${REPO_DIR}/venv/bin/python"
else
  PY="$(command -v python3)"
fi
exec "${PY}" "${REPO_DIR}/scripts/feedback_retrain_once.py"
