#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8766}"
TARGET_TIME="${TARGET_TIME:-22:20}"
ADB_PATH="${ADB_PATH:-/Users/username/Library/Android/sdk/platform-tools/adb}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
REDIS_PREFIX="${REDIS_PREFIX:-ice5g}"
APP="./realtime_ice_5g_osm_map_app.py"

now_epoch="$(date +%s)"
target_epoch="$(date -j -f '%Y-%m-%d %H:%M' "$(date '+%Y-%m-%d') ${TARGET_TIME}" +%s)"
if [ "$target_epoch" -lt "$now_epoch" ]; then
  target_epoch="$now_epoch"
fi

sleep_for=$((target_epoch - now_epoch))
echo "Release deploy target: ${TARGET_TIME}; sleeping ${sleep_for}s"
if [ "$sleep_for" -gt 0 ]; then
  sleep "$sleep_for"
fi

echo "Stopping existing dashboard on port ${PORT}"
pkill -f "realtime_ice_5g_osm_map_app.py --port ${PORT}" 2>/dev/null || true
sleep 1

echo "Starting release dashboard"
exec "$APP" \
  --port "$PORT" \
  --interval 10 \
  --adb "$ADB_PATH" \
  --redis-url "$REDIS_URL" \
  --redis-prefix "$REDIS_PREFIX"
