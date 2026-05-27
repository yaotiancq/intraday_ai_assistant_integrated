#!/usr/bin/env bash
set -euo pipefail

cd /app
mkdir -p data logs

RUN_TIME="${PREMARKET_RUN_TIME:-05:45}"
TIMEZONE="${TIMEZONE:-America/Los_Angeles}"
INTERVAL_SECONDS="${PREMARKET_LOOP_INTERVAL_SECONDS:-${PREMARKET_SCHEDULER_POLL_SECONDS:-30}}"

echo "[premarket-loop] started"
echo "[premarket-loop] timezone=$TIMEZONE run_time=$RUN_TIME interval=${INTERVAL_SECONDS}s"

run_premarket_once() {
  local force_run="${1:-false}"

  CMD=(python scripts/run_premarket.py)

  if [ "${PREMARKET_SEND_DISCORD:-true}" = "true" ]; then
    CMD+=(--send-discord)
  fi

  if [ "${PREMARKET_SEND_TO_MONITOR:-true}" = "true" ]; then
    CMD+=(--send-to-monitor)
  fi

  if [ "${PREMARKET_DRY_RUN:-false}" = "true" ]; then
    CMD+=(--dry-run)
  fi

  if [ "$force_run" = "true" ]; then
    CMD+=(--force-run --allow-non-trading-day-test)
  fi

  echo "[premarket-loop] exec: ${CMD[*]}"
  "${CMD[@]}" 2>&1 | tee -a logs/premarket.log
}

if [ "${PREMARKET_TEST_RUN_ON_START:-false}" = "true" ]; then
  echo "[premarket-loop] PREMARKET_TEST_RUN_ON_START=true, running once immediately"
  run_premarket_once true || true
fi

while true; do
  NOW="$(TZ="$TIMEZONE" date +%H:%M)"
  TODAY="$(TZ="$TIMEZONE" date +%F)"
  FLAG_FILE="data/.premarket_ran_${TODAY}"

  if [ "$NOW" = "$RUN_TIME" ] && [ ! -f "$FLAG_FILE" ]; then
    echo "[premarket-loop] running premarket task at ${TODAY} ${NOW} ${TIMEZONE}"

    run_premarket_once false

    touch "$FLAG_FILE"
  fi

  sleep "$INTERVAL_SECONDS"
done