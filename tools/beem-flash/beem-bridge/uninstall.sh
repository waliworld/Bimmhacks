#!/bin/sh
# uninstall.sh - stop and remove the BeeM-Flash bridge helper. RUN AS ROOT.
# Stops it with SIGTERM first so it takes its Wi-Fi network down, removes the
# firewall rules it added and deletes its virtual interface itself; anything
# left over (a crash) is cleaned up afterwards.
set -u
DIR=/var/opt/beem-bridge
[ "$(id -u)" = "0" ] || { echo "run this as root"; exit 1; }

if command -v systemctl >/dev/null 2>&1; then
  systemctl stop beem-bridged.service 2>/dev/null
  systemctl disable beem-bridged.service 2>/dev/null
  rm -f /etc/systemd/system/beem-bridged.service
  systemctl daemon-reload 2>/dev/null
fi
if [ -f "$DIR/bridge.pid" ]; then
  P="$(cat "$DIR/bridge.pid")"; kill "$P" 2>/dev/null
  i=0; while kill -0 "$P" 2>/dev/null && [ $i -lt 15 ]; do sleep 1; i=$((i+1)); done
  kill -9 "$P" 2>/dev/null
fi

# leftovers, only if the helper could not clean up after itself
pkill -f /run/beem-bridge/hostapd.conf 2>/dev/null
pkill -f /run/beem-bridge/dnsmasq 2>/dev/null
command -v iw >/dev/null 2>&1 && iw dev beem0 del 2>/dev/null

rm -rf "$DIR" /run/beem-bridge /dev/shm/beem-bridge
echo "beem-bridged stopped and removed. Its Wi-Fi network is down; the car's own Wi-Fi is untouched."
