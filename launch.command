#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ "${1:-}" != "--background" ]]; then
  dashboard="http://127.0.0.1:${JOBHUNT_PORT:-8765}"
  if /usr/bin/curl --fail --silent --max-time 2 "$dashboard/api/state" >/dev/null; then
    exec /usr/bin/open "$dashboard"
  fi
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import pypdf, playwright' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ -z "${JOBHUNT_CODEX:-}" ]] && ! command -v codex >/dev/null; then
  codex_bins=("$HOME"/.vscode/extensions/openai.chatgpt-*/bin/macos-*/codex(NOm))
  if (( ${#codex_bins} )); then
    export JOBHUNT_CODEX="${codex_bins[1]}"
  fi
fi
if [[ "${1:-}" == "--background" ]]; then
  exec .venv/bin/python app.py
fi
exec .venv/bin/python app.py --open
