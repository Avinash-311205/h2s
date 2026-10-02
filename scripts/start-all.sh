#!/usr/bin/env bash
# Start Modules 1-4 in dependency order and wait for each to report healthy.
#
# The modules are independent services that talk over Redis; there is no import
# between them. Each is started detached by scripts/run_service.py so it
# survives the shell that launched it.
#
#   ./scripts/start-all.sh          # start anything not already running
#   ./scripts/start-all.sh --status # what is up
#   ./scripts/stop-all.sh           # stop everything
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$ROOT/scripts/run_service.py"
PYTHON="${PYTHON:-python3}"

# name | module dir | port
SERVICES=(
  "module1|module-1-citizen-ingestion/backend|8001"
  "module2|module-2-ai-understanding|8002"
  "module3|module-3-civic-data-processing|8003"
  "module4|module-4-national-data-mesh|8004"
)

log()  { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; }

if [[ "${1:-}" == "--status" ]]; then
  "$PYTHON" "$RUNNER" --status
  exit 0
fi

# Startup includes creating tables and dialing Redis; on a slow or busy disk
# that can take a while, so the budget is generous rather than optimistic.
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

wait_for_health() {
  local port="$1" name="$2" deadline=$((SECONDS + HEALTH_TIMEOUT)) code
  while (( SECONDS < deadline )); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/api/v1/health" 2>/dev/null)"
    # 503 still means the process is up and answering; it reports a real
    # dependency problem, which we surface rather than treat as "not started".
    if [[ "$code" == "200" || "$code" == "503" ]]; then
      log "  $name is answering on $port (health $code)"
      return 0
    fi
    sleep 1
  done
  fail "$name did not become healthy on port $port within ${HEALTH_TIMEOUT}s"
  fail "  last log lines from .run/logs/$name.log:"
  tail -n 15 "$ROOT/.run/logs/$name.log" 2>/dev/null | sed 's/^/    /' >&2
  return 1
}

log "Starting the civic pipeline against Redis at ${REDIS_URL:-redis://localhost:6379/0}"

started=0
for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name dir port <<< "$entry"

  # A live listener on the port means the module is already serving; restarting
  # it would drop the pipeline mid-flight.
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    log "  $name already listening on $port"
    continue
  fi

  log "  starting $name (:$port)"
  if ! ( cd "$ROOT/$dir" && "$PYTHON" "$RUNNER" "$name" \
         "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$port" ); then
    fail "could not start $name"
    exit 1
  fi
  started=$((started + 1))
  wait_for_health "$port" "$name" || exit 1
done

log ""
if (( started == 0 )); then
  log "Nothing to do; all four services were already up."
else
  log "Started $started service(s)."
fi
log "  module1 http://127.0.0.1:8001/api/v1/docs   citizen submission"
log "  module2 http://127.0.0.1:8002/api/v1/docs   understanding"
log "  module3 http://127.0.0.1:8003/api/v1/docs   civic processing"
log "  module4 http://127.0.0.1:8004/api/v1/docs   national data mesh"
