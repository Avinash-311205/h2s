#!/usr/bin/env bash
# Stop every service started by scripts/start-all.sh.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

# name | port
SERVICES=(
  "module1|8001"
  "module2|8002"
  "module3|8003"
  "module4|8004"
)

for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name port <<< "$entry"

  # Ask the tracked process first...
  "$PYTHON" "$ROOT/scripts/run_service.py" --stop "$name" >/dev/null 2>&1 || true

  # ...then make sure the port is actually free. A leftover listener would
  # silently shadow the next start with stale code, so be blunt about it.
  for _ in 1 2 3; do
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)"
    [[ -z "$pids" ]] && break
    kill $pids 2>/dev/null || true
    sleep 1
  done
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)"
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi

  if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    printf '  %s: port %s is still in use\n' "$name" "$port" >&2
  else
    printf '  %s stopped (port %s free)\n' "$name" "$port"
  fi
done

rm -rf "$ROOT/.run"

echo "All pipeline services stopped (Redis is left running)."
