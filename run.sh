#!/usr/bin/env bash
# Launch the Agent Desktop Workbench.
#   ./run.sh                      # default port 8787
#   WORKBENCH_PORT=9000 ./run.sh  # custom port
#   PYTHON=/path/to/python3 ./run.sh   # custom interpreter (needs pydantic)
set -euo pipefail
cd "$(dirname "$0")"

have_pydantic() {
  "$1" -c "import pydantic" >/dev/null 2>&1
}

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in ./.venv/bin/python ../.venv/bin/python "$(command -v python3)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ] && have_pydantic "$candidate"; then
      PY="$candidate"
      break
    fi
  done
fi
if [ -z "$PY" ] || ! have_pydantic "$PY"; then
  echo "未找到带 pydantic 的 python3。安装依赖或用 PYTHON=/path/to/python3 ./run.sh" >&2
  exit 1
fi

exec "$PY" server.py "$@"
