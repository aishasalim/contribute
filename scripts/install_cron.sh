#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
MARKER="# contribute-hermes-managed"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "Missing $ROOT/.env; copy .env.example and configure it first." >&2
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing $PYTHON; run 'uv sync --extra test && uv run playwright install chromium'." >&2
  exit 1
fi

mkdir -p "$ROOT/artifacts"
existing="$(crontab -l 2>/dev/null || true)"
cleaned="$(printf '%s\n' "$existing" | awk '
  $0 == "# contribute-hermes-managed begin" {skip=1; next}
  $0 == "# contribute-hermes-managed end" {skip=0; next}
  !skip {print}
')"

{
  printf '%s\n' "$cleaned"
  echo "$MARKER begin"
  echo "CRON_TZ=America/Chicago"
  echo "* * * * * cd \"$ROOT\" && set -a && . ./.env && set +a && \"$PYTHON\" -m api.notify >> artifacts/notify.log 2>&1"
  for hour in 11 13 15 17 19; do
    echo "0 $hour * * * cd \"$ROOT\" && set -a && . ./.env && set +a && \"$PYTHON\" -m hermes.jobs harvest_apply >> artifacts/cron.log 2>&1"
  done
  echo "0 20 * * * cd \"$ROOT\" && set -a && . ./.env && set +a && \"$PYTHON\" -m hermes.jobs gmail >> artifacts/cron.log 2>&1"
  echo "$MARKER end"
} | crontab -

echo "Installed Hermes cron schedule in America/Chicago."
