#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${1:-$HOME/Developer/mediapipe}"
BLE_LOG="${BLE_LOG:-/tmp/ble_bridge.log}"

echo "== BLE health check =="
echo "project_root=$PROJECT_ROOT"
echo "ble_log=$BLE_LOG"
echo

if command -v pgrep >/dev/null 2>&1; then
  echo "-- bridge processes --"
  pgrep -af 'ble\.ble_bridge_server' || echo "none"
  echo
fi

echo "-- bluetoothctl show --"
bluetoothctl show || true
echo

echo "-- recent bridge log --"
if systemctl is-active --quiet sleepydrive-ble-bridge.service 2>/dev/null; then
  journalctl -u sleepydrive-ble-bridge.service -n 80 --no-pager
elif [[ -f "$BLE_LOG" ]]; then
  tail -n 80 "$BLE_LOG"
else
  echo "missing: $BLE_LOG"
fi
echo

echo "-- startup markers --"
if systemctl is-active --quiet sleepydrive-ble-bridge.service 2>/dev/null; then
  journalctl -u sleepydrive-ble-bridge.service -n 200 --no-pager \
    | grep -E "BLE GATT application registered|BLE advertisement registered as|BLE bridge ready|BLE notifier failed to start|GATT registration failed|BLE advert failed" || true
elif [[ -f "$BLE_LOG" ]]; then
  grep -E "BLE GATT application registered|BLE advertisement registered as|BLE bridge ready|BLE notifier failed to start|GATT registration failed|BLE advert failed" "$BLE_LOG" || true
fi
