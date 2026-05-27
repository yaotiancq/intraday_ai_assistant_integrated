#!/usr/bin/env bash
set -euo pipefail

cd /app
mkdir -p data logs

RUN_TIME="${PREMARKET_RUN_TIME:-05:45}"
TIMEZONE="${TIMEZONE:-America/Los_Angeles}"
INTERVAL_SECONDS="${PREMARKET_LOOP_INTERVAL_SECONDS:-30}"

echo "[premarket-loop] started"
echo "[premarket-loop] timezone=$TIMEZONE run_time=$RUN_TIME interval=${INTERVAL_SECONDS}s"

if [ "${PREMARKET_TEST_RUN_ON_START:-false}" = "true" ]; then
  echo "[premarket-loop] PREMARKET_TEST_RUN_ON_START=true, running once immediately"

  python scripts/run_premarket.py \
    --dry-run \
    --force-run \
    --allow-non-trading-day-test \
    --send-to-monitor \
    2>&1 | tee -a logs/premarket.log || true
fi

while true; do
  NOW="$(TZ="$TIMEZONE" date +%H:%M)"
  TODAY="$(TZ="$TIMEZONE" date +%F)"
  FLAG_FILE="data/.premarket_ran_${TODAY}"

  if [ "$NOW" = "$RUN_TIME" ] && [ ! -f "$FLAG_FILE" ]; then
    echo "[premarket-loop] running premarket task at ${TODAY} ${NOW} ${TIMEZONE}"

    python scripts/run_premarket.py \
      --send-to-monitor \
      2>&1 | tee -a logs/premarket.log

    touch "$FLAG_FILE"
  fi

  sleep "$INTERVAL_SECONDS"
done
