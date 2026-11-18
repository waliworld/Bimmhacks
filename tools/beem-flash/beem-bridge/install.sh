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
#   --open-iface LIST  open the bridge ports (tcp 6801/13400, udp 6811/13400) in the
#                      firewall on these interfaces, comma-separated. "wifi" = every
#                      Wi-Fi interface: the car's own Wi-Fi (wlan_bridge) AND a phone
#                      hotspot the car joins (wlan1). BMW units drop unknown ports on
#                      Wi-Fi, so tools need this. Removed again when the helper stops.
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
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop beem-bridged.service 2>/dev/null; systemctl reset-failed beem-bridged.service 2>/dev/null
fi
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

# 3) start. In order of preference:
#    a) a boot service in /etc/systemd/system  (only if /etc is writable)
#    b) a transient systemd unit (systemd-run): supervised and restarted on
#       failure, needs no write to /etc - but does not survive a head-unit restart
#    c) nohup / plain background start (no supervision)
# Head units with a read-only /etc and no setsid (BusyBox) used to end up with
# nothing running at all; every path below is checked.
STARTED="" BOOT=""
if [ "$USE_SYSTEMD" = "1" ] && [ -w "$(dirname "$UNIT")" ]; then
  if sed -e "s|__NODE__|$NODE|g" -e "s|__ARGS__|$ARGS|g" "$SELF_DIR/beem-bridged.service" > "$UNIT" 2>/dev/null; then
    systemctl daemon-reload || die "systemctl daemon-reload failed"
    systemctl enable beem-bridged.service >/dev/null 2>&1 && BOOT=1 || echo "warning: could not enable at boot"
    systemctl restart beem-bridged.service || die "the service did not start: journalctl -u beem-bridged -e"
    STARTED=systemd
  fi
fi
if [ -z "$STARTED" ] && [ "$USE_SYSTEMD" = "1" ] && command -v systemd-run >/dev/null 2>&1; then
  [ -w "$(dirname "$UNIT")" ] || echo "note: /etc is read-only on this unit - starting a transient service instead (until the next restart)"
  systemctl stop beem-bridged.service 2>/dev/null; systemctl reset-failed beem-bridged.service 2>/dev/null
  # shellcheck disable=SC2086
  if systemd-run --unit=beem-bridged -p Restart=always -p RestartSec=5 "$NODE" "$DIR/beem-bridged.js" $ARGS >/dev/null 2>&1; then
    STARTED=transient
  else
    echo "warning: systemd-run failed - starting it in the background instead"
  fi
fi
if [ -z "$STARTED" ]; then
  # The helper ignores SIGHUP, so closing this shell does not stop it.
  if command -v nohup >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    nohup "$NODE" "$DIR/beem-bridged.js" $ARGS </dev/null >"$DIR/bridge.log" 2>&1 &
  else
    # shellcheck disable=SC2086
    "$NODE" "$DIR/beem-bridged.js" $ARGS </dev/null >"$DIR/bridge.log" 2>&1 &
  fi
  echo $! > "$DIR/bridge.pid"
  STARTED=background
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
  echo " beem-bridged was started ($STARTED) but is NOT answering yet."
fi
case "$STARTED" in
  systemd)   echo " log    : journalctl -u beem-bridged -e" ;;
  transient) echo " log    : journalctl -u beem-bridged -e" ;;
  *)         echo " log    : $DIR/bridge.log" ;;
esac
if [ -n "$BOOT" ]; then
  echo " boot   : starts at every head-unit start"
else
  echo " boot   : NOT started at boot on this unit (read-only /etc) - run 'sh install.sh' again after a restart"
fi
echo " status : sh install.sh --status"
echo " remove : sh uninstall.sh"
echo "=================================================================="
# Does the car's firewall let tools reach the bridge on Wi-Fi?
FWOUT="$("$NODE" "$DIR/beem-bridged.js" --fw-check 2>&1)"; FWRC=$?
if [ -n "$FWOUT" ]; then
  echo
  echo "Car firewall, bridge ports on Wi-Fi:"
  echo "$FWOUT" | sed 's/^\[beem-bridged\] /  /'
  if [ "$FWRC" = "2" ]; then
    echo
    echo "!! The car's firewall DROPS the bridge ports on Wi-Fi - MHD / ENET tools cannot connect."
    echo "!! Re-run the install with:   sh install.sh --open-iface wifi"
  fi
fi
# What the helper can actually do on THIS unit
if ! command -v hostapd >/dev/null 2>&1 || ! command -v iw >/dev/null 2>&1; then
  echo
  echo "NOTE: this head unit has no hostapd/iw, so it cannot host its own Wi-Fi network:"
  echo "      WI-FI: OWN will report that. The helper is only useful here to open the"
  echo "      firewall on the car's own Wi-Fi:  sh install.sh --open-iface wlan_bridge"
  [ -n "$OPEN_IFACE" ] && echo "      (done: opened on $OPEN_IFACE)"
fi
echo
echo "NEXT, on the car: BeeM-Flash > CONNECT > WIRELESS BRIDGE > BRIDGE: ON."
[ -n "$OK" ] || exit 1
