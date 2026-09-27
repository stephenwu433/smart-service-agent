#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${SCRIPT_DIR}/common.sh"
require_venv

if ! "${VENV_DIR}/bin/python" -c "import streamlit" >/dev/null 2>&1; then
  log_info "安装 Streamlit Demo dependency"
  "${VENV_DIR}/bin/pip" install 'streamlit>=1.32,<2.0'
fi

ADDRESS="${STREAMLIT_ADDRESS:-127.0.0.1}"
PORT="${STREAMLIT_PORT:-8501}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
cd "${PROJECT_ROOT}"
log_info "Streamlit Demo: http://${ADDRESS}:${PORT}"
exec "${VENV_DIR}/bin/streamlit" run "${PROJECT_ROOT}/streamlit_app.py" \
  --server.headless true \
  --server.address "${ADDRESS}" \
  --server.port "${PORT}"
