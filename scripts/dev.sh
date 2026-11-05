#!/usr/bin/env bash
# Local dev supervisor for Casey.
#
#   scripts/dev.sh up        start opencode serve (credential-watching) + slack run
#   scripts/dev.sh down      stop both
#   scripts/dev.sh restart   restart opencode serve now (re-reads credentials)
#   scripts/dev.sh status    show what is running
#   scripts/dev.sh logs      tail both logs
#
# opencode serve only reads provider credentials from its environment at start,
# so the serve loop sources CASEY_CREDENTIALS_FILE and restarts opencode whenever
# that file changes (e.g. the weekly token refresh). slack run keeps running; the
# Python client retries while opencode comes back.
#
# Credential values are never printed.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_DIR/.run"
ENV_FILE="${CASEY_ENV_FILE:-$PROJECT_DIR/.env}"

# Deployment-specific values live in .env (gitignored; see .env.sample).
if [[ -r "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

CREDS_FILE="${CASEY_CREDENTIALS_FILE:-}"
OPENCODE_PORT="${OPENCODE_PORT:-4096}"
OPENCODE_BIN="${OPENCODE_BIN:-opencode}"
SLACK_BIN="${SLACK_BIN:-$HOME/.slack/bin/slack}"
SLACK_APP_ID="${SLACK_APP_ID:-}"
SLACK_RUN_FLAGS="${SLACK_RUN_FLAGS:-}"
POLL_SECONDS="${CASEY_CREDS_POLL_SECONDS:-15}"

LOOP_PID="$RUN_DIR/opencode-loop.pid"
SLACK_PID="$RUN_DIR/slack-run.pid"
OPENCODE_LOG="$RUN_DIR/opencode-serve.log"
SLACK_LOG="$RUN_DIR/slack-run.log"
SUPERVISOR_LOG="$RUN_DIR/supervisor.log"

mkdir -p "$RUN_DIR"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$SUPERVISOR_LOG" >&2
}

alive() {
  local pidfile=$1
  [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

healthy() {
  curl -fsS -o /dev/null "http://127.0.0.1:$OPENCODE_PORT/global/health" 2>/dev/null
}

wait_healthy() {
  local tries=${1:-30}
  for ((i = 0; i < tries; i++)); do
    healthy && return 0
    sleep 1
  done
  return 1
}

start_opencode_with_creds() {
  (
    set +u
    # shellcheck disable=SC1090
    source "$CREDS_FILE" >/dev/null 2>&1
    exec "$OPENCODE_BIN" serve --port "$OPENCODE_PORT" --hostname 127.0.0.1
  ) >>"$OPENCODE_LOG" 2>&1 &
  echo $!
}

serve_loop() {
  local child="" seen=""
  trap 'if [[ -n "$child" ]]; then kill "$child" 2>/dev/null; wait "$child" 2>/dev/null; fi; exit 0' TERM INT

  while true; do
    local current
    current=$(stat -c %Y "$CREDS_FILE" 2>/dev/null || echo "missing")

    if [[ "$current" != "$seen" ]]; then
      if [[ -n "$child" ]]; then
        log "credentials file changed; restarting opencode serve"
        kill "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
      fi
      if [[ "$current" == "missing" ]]; then
        log "credentials file not found at $CREDS_FILE; retrying in ${POLL_SECONDS}s"
        child=""
      else
        child=$(start_opencode_with_creds)
        log "opencode serve started (pid $child) on port $OPENCODE_PORT"
      fi
      seen=$current
    elif [[ -n "$child" ]] && ! kill -0 "$child" 2>/dev/null; then
      log "opencode serve exited unexpectedly; restarting in 5s"
      sleep 5
      seen=""
      child=""
      continue
    fi

    sleep "$POLL_SECONDS"
  done
}

cmd_up() {
  if [[ -z "$CREDS_FILE" || ! -r "$CREDS_FILE" ]]; then
    log "CASEY_CREDENTIALS_FILE is unset or not readable (set it in $ENV_FILE)"
    exit 1
  fi
  if [[ -z "$SLACK_APP_ID" ]]; then
    log "SLACK_APP_ID is unset (set it in $ENV_FILE)"
    exit 1
  fi

  if alive "$LOOP_PID"; then
    log "opencode loop already running (pid $(cat "$LOOP_PID"))"
  else
    setsid nohup "$0" __serve_loop >/dev/null 2>&1 </dev/null &
    echo $! >"$LOOP_PID"
    log "opencode loop started (pid $(cat "$LOOP_PID")), watching $CREDS_FILE"
  fi

  if wait_healthy 40; then
    log "opencode serve healthy at http://127.0.0.1:$OPENCODE_PORT"
  else
    log "opencode serve did not become healthy; see $OPENCODE_LOG"
    exit 1
  fi

  if alive "$SLACK_PID"; then
    log "slack run already running (pid $(cat "$SLACK_PID"))"
  else
    (
      cd "$PROJECT_DIR"
      # shellcheck disable=SC2086
      setsid nohup "$SLACK_BIN" run --app="$SLACK_APP_ID" --manifest-source=remote \
        $SLACK_RUN_FLAGS >>"$SLACK_LOG" 2>&1 </dev/null &
      echo $! >"$SLACK_PID"
    )
    log "slack run started (pid $(cat "$SLACK_PID"))"
  fi
}

stop_pidfile() {
  local pidfile=$1 name=$2
  if alive "$pidfile"; then
    local pid
    pid=$(cat "$pidfile")
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for ((i = 0; i < 20; i++)); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    log "$name stopped (pid $pid)"
  else
    log "$name not running"
  fi
  rm -f "$pidfile"
}

cmd_down() {
  stop_pidfile "$SLACK_PID" "slack run"
  stop_pidfile "$LOOP_PID" "opencode loop"
}

cmd_restart() {
  if ! alive "$LOOP_PID"; then
    log "opencode loop not running; use: $0 up"
    exit 1
  fi
  touch "$CREDS_FILE"
  log "requested opencode restart; the loop will pick it up within ${POLL_SECONDS}s"
  sleep "$((POLL_SECONDS + 2))"
  if wait_healthy 40; then
    log "opencode serve healthy after restart"
  else
    log "opencode serve not healthy after restart; see $OPENCODE_LOG"
    exit 1
  fi
}

cmd_status() {
  if alive "$LOOP_PID"; then
    echo "opencode loop : running (pid $(cat "$LOOP_PID")), watching $CREDS_FILE"
  else
    echo "opencode loop : stopped"
  fi
  if healthy; then
    echo "opencode serve: healthy on http://127.0.0.1:$OPENCODE_PORT"
  else
    echo "opencode serve: not responding on port $OPENCODE_PORT"
  fi
  if alive "$SLACK_PID"; then
    echo "slack run     : running (pid $(cat "$SLACK_PID"))"
  else
    echo "slack run     : stopped"
  fi
  echo "credentials   : $CREDS_FILE (modified $(stat -c '%y' "$CREDS_FILE" 2>/dev/null | cut -d. -f1 || echo missing))"
}

cmd_logs() {
  tail -n 40 -F "$SUPERVISOR_LOG" "$OPENCODE_LOG" "$SLACK_LOG"
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  __serve_loop) serve_loop ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
