#!/bin/sh
# install.sh - install the BeeM-Flash bridge helper on the head unit. RUN AS ROOT.
#
#   sh install.sh --probe            report what this unit can do; changes NOTHING
#   sh install.sh                    install + start at boot (systemd), verify it answers
#   sh install.sh --status           ask the running helper for its state
#
# Options for an install:
#   --app-uid N        uid the BeeM-Flash app runs as (default 12261; shown on the
#                      app's SETUP > DIAG page, line P8 "uid ...")
#   --open-iface LIST  also open the bridge ports in the firewall on these existing
#                      interfaces, comma-separated (e.g. wlan_bridge = the car's own
#                      Wi-Fi). Use only if a phone on the car's Wi-Fi cannot reach it.
#   --channel N        fix the Wi-Fi channel (default: follow the car's own Wi-Fi)
#   --take-iface       allow taking over the Wi-Fi radio if it cannot run a separate
#                      access point (drops the car's own Wi-Fi / CarPlay while on)
#   --no-systemd       start detached instead of as a systemd service
#
# Nothing is broadcast after install: the helper waits until the app turns
# "WI-FI: OWN" on (CONNECT > WIRELESS BRIDGE). See INSTALL.md.
set -u
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR=/var/opt/beem-bridge
UNIT=/etc/systemd/system/beem-bridged.service
NODE="${NODE:-/usr/bin/node}"
[ -x "$NODE" ] || NODE="$(command -v node 2>/dev/null)"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -n "$NODE" ] && [ -x "$NODE" ] || die "node not found; run with NODE=/path/to/node sh install.sh"
[ "$(id -u)" = "0" ] || die "run this as root (use your SSH-root / root-shell tool)"

MODE=install APP_UID=12261 OPEN_IFACE="" TAKE="" CHANNEL="" USE_SYSTEMD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) MODE=probe ;;
    --status) MODE=status ;;
    --app-uid) shift; APP_UID="${1:-}" ;;
    --open-iface) shift; OPEN_IFACE="${1:-}" ;;
    --take-iface) TAKE="--take-iface" ;;
    --channel) shift; CHANNEL="${1:-}" ;;
    --no-systemd) USE_SYSTEMD=0 ;;
    *) die "unknown option: $1 (see the top of this file)" ;;
  esac
  shift
done
case "$APP_UID" in ''|*[!0-9]*) die "--app-uid needs a number" ;; esac
case "$OPEN_IFACE" in *[!A-Za-z0-9_.,-]*) die "--open-iface: interface names only" ;; esac
case "$CHANNEL" in *[!0-9]*) die "--channel needs a number" ;; esac

if [ "$MODE" = "probe" ]; then
  # Runs straight from where you unpacked it; copies and changes nothing.
  exec "$NODE" "$SELF_DIR/beem-bridged.js" --probe
fi
if [ "$MODE" = "status" ]; then
  exec "$NODE" "$DIR/beem-bridged.js" --status
fi

ARGS="--uid $APP_UID"
[ -n "$OPEN_IFACE" ] && ARGS="$ARGS --open-iface $OPEN_IFACE"
[ -n "$TAKE" ] && ARGS="$ARGS $TAKE"
[ -n "$CHANNEL" ] && ARGS="$ARGS --channel $CHANNEL"
command -v systemctl >/dev/null 2>&1 || USE_SYSTEMD=0

# 1) stop any earlier copy (either kind) so only one runs
if command -v systemctl >/dev/null 2>&1; then systemctl stop beem-bridged.service 2>/dev/null; fi
if [ -f "$DIR/bridge.pid" ]; then
  P="$(cat "$DIR/bridge.pid")"; kill "$P" 2>/dev/null
  i=0; while kill -0 "$P" 2>/dev/null && [ $i -lt 15 ]; do sleep 1; i=$((i+1)); done
  rm -f "$DIR/bridge.pid"
fi

# 2) files
mkdir -p "$DIR" || die "cannot create $DIR"
cp "$SELF_DIR/beem-bridged.js" "$DIR/beem-bridged.js" || die "cannot copy beem-bridged.js to $DIR"
chmod 0755 "$DIR/beem-bridged.js"
"$NODE" -e "require('$DIR/beem-bridged.js')" || die "the helper does not load under $NODE"

# 3) start (systemd if possible, detached otherwise)
STARTED=""
if [ "$USE_SYSTEMD" = "1" ]; then
  if sed -e "s|__NODE__|$NODE|g" -e "s|__ARGS__|$ARGS|g" "$SELF_DIR/beem-bridged.service" > "$UNIT" 2>/dev/null; then
    systemctl daemon-reload || die "systemctl daemon-reload failed"
    systemctl enable beem-bridged.service >/dev/null 2>&1 || echo "warning: could not enable at boot"
    systemctl restart beem-bridged.service || die "the service did not start: journalctl -u beem-bridged -e"
    STARTED=systemd
  else
    echo "warning: cannot write $UNIT (read-only /etc?) - starting detached instead"
  fi
fi
# Detach WITHOUT assuming setsid: a head unit's busybox may ship neither setsid nor
# nohup, and a bare "setsid: not found" left no daemon running at all while the
# install still reported a pid (seen on an MGU with a read-only /etc).
start_detached() {
  if command -v setsid >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    setsid "$NODE" "$DIR/beem-bridged.js" $ARGS >"$DIR/bridge.log" 2>&1 &
  elif command -v nohup >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    nohup "$NODE" "$DIR/beem-bridged.js" $ARGS >"$DIR/bridge.log" 2>&1 &
  else
    trap '' HUP                     # last resort: survive the ssh session closing
    # shellcheck disable=SC2086
    "$NODE" "$DIR/beem-bridged.js" $ARGS >"$DIR/bridge.log" 2>&1 &
  fi
  echo $! > "$DIR/bridge.pid"
}

if [ -z "$STARTED" ]; then
  start_detached
  sleep 1
  BGPID="$(cat "$DIR/bridge.pid" 2>/dev/null)"
  if [ -z "$BGPID" ] || ! kill -0 "$BGPID" 2>/dev/null; then
    echo "warning: the detached helper did not stay up - see $DIR/bridge.log"
  fi
  STARTED=detached
fi

# 4) verify the helper answers on its control socket
i=0; OK=""
while [ $i -lt 10 ]; do
  if "$NODE" "$DIR/beem-bridged.js" --status >/dev/null 2>&1; then OK=1; break; fi
  sleep 1; i=$((i+1))
done

echo "=================================================================="
if [ -n "$OK" ]; then
  echo " beem-bridged is installed and answering ($STARTED)."
else
  echo " beem-bridged was installed but is NOT answering yet."
  [ "$STARTED" = "systemd" ] && echo " check: journalctl -u beem-bridged -e" || echo " check: $DIR/bridge.log"
fi
echo " status : sh install.sh --status"
[ "$STARTED" = "systemd" ] && echo " log    : journalctl -u beem-bridged -e   (starts at every boot)" \
                          || echo " log    : $DIR/bridge.log   (detached: does NOT start at boot)"
echo " remove : sh uninstall.sh"
echo "=================================================================="
echo
echo "NEXT, on the car: BeeM-Flash > CONNECT > WIRELESS BRIDGE > BRIDGE: ON, then WI-FI: OWN."
echo "Join the network shown on the phone, and point the tool at the address and port shown."
[ -n "$OK" ] || exit 1
