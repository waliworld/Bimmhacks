#!/bin/sh
# Bimmhacks Pocket - turn a small Linux box (Raspberry Pi OS / Debian / Ubuntu) into the box on the
# ENET cable that serves the phone page. Run it ONCE, as root, from the unzipped package:
#
#     sudo sh installer/pocket/setup-box.sh [--ssid NAME] [--pass WIFI-PASSWORD] [--pin 2468]
#                                           [--no-hotspot] [--enet-if eth0] [--wifi-if wlan0] [--port 8080]
#
# What it does (each step is skipped when already done):
#   1. installs what the installer needs: python3 (+ cryptography), Node.js, the OpenSSH client, unzip,
#      NetworkManager and avahi (so the box answers as bimmhacks.local)
#   2. gives the Ethernet port a link-local (169.254.x.x) address: that is where the car's ENET gateway is
#   3. starts a Wi-Fi hotspot the phone joins (unless --no-hotspot: then join the box to your own Wi-Fi
#      with `nmcli dev wifi connect <ssid> password <pw>` and open http://bimmhacks.local:8080)
#   4. runs the Pocket server as a service that starts with the box
#
# Tested logic, untested hardware: this script was written against NetworkManager's documented commands
# and checked for syntax; run it on your own Pi once and read what it prints. Nothing here touches the car.
set -eu

SSID="Bimmhacks-Pocket"
PASS="bimmhacks"
PIN=""
HOTSPOT=1
ENET_IF="eth0"
WIFI_IF="wlan0"
PORT="8080"
while [ $# -gt 0 ]; do
    case "$1" in
        --ssid) SSID="$2"; shift 2 ;;
        --pass) PASS="$2"; shift 2 ;;
        --pin) PIN="$2"; shift 2 ;;
        --no-hotspot) HOTSPOT=0; shift ;;
        --enet-if) ENET_IF="$2"; shift 2 ;;
        --wifi-if) WIFI_IF="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        -h|--help) sed -n 2,20p "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root:  sudo sh $0 ..." >&2; exit 1; }
[ ${#PASS} -ge 8 ] || { echo "--pass needs at least 8 characters (WPA2)" >&2; exit 2; }

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)                      # the unzipped package
SERVER="$ROOT/installer/pocket_server.py"
[ -f "$SERVER" ] || { echo "cannot find $SERVER - run this from the unzipped Bimmhacks package" >&2; exit 1; }
echo "Bimmhacks package: $ROOT"

# 1. packages ---------------------------------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -y -q python3 python3-cryptography nodejs openssh-client unzip network-manager avahi-daemon
else
    echo "no apt-get: install python3 (+cryptography), nodejs, openssh-client, NetworkManager and avahi yourself" >&2
fi
python3 -c 'import cryptography' 2>/dev/null || python3 -m pip install --break-system-packages cryptography || python3 -m pip install cryptography
NODE_MAJOR=$(node -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo 0)
[ "$NODE_MAJOR" -ge 14 ] || echo "WARNING: Node.js 14 or newer is needed for the App Store step (found: $(node --version 2>/dev/null || echo none)). Raspberry Pi OS 'bookworm' or newer ships a usable one." >&2
systemctl enable --now NetworkManager >/dev/null 2>&1 || true
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true

# 1b. the Store package and Doom's game data, downloaded now while the box has internet: on the car the box
#     is alone with the car (its Wi-Fi is the hotspot), so the App Store step installs from this copy.
#     Skipped when offline; the phone page has a "Download the Store package to the box" button for later.
if python3 "$ROOT/installer/pocket_server.py" --stage-store; then
    echo "Store package staged in $ROOT/pocket-store"
else
    echo "WARNING: could not download the Store package now (no internet?). Do it later from the phone page, with the box online." >&2
fi

# 2. the Ethernet port talks to the car: link-local, no DHCP (the ENET gateway is at 169.254.x.x) -----
if command -v nmcli >/dev/null 2>&1; then
    if nmcli -t -f NAME con show | grep -qx "bimmhacks-enet"; then
        nmcli con modify bimmhacks-enet ipv4.method link-local ipv6.method disabled connection.autoconnect yes
    else
        nmcli con add type ethernet ifname "$ENET_IF" con-name bimmhacks-enet ipv4.method link-local ipv6.method disabled connection.autoconnect yes
    fi
    nmcli con up bimmhacks-enet >/dev/null 2>&1 || echo "(the ENET port $ENET_IF comes up when the cable is plugged into a car)"

    # 3. the Wi-Fi the phone joins ------------------------------------------------------------------
    if [ "$HOTSPOT" = 1 ]; then
        if nmcli -t -f NAME con show | grep -qx "bimmhacks-hotspot"; then
            nmcli con modify bimmhacks-hotspot 802-11-wireless.ssid "$SSID" wifi-sec.psk "$PASS"
        else
            nmcli con add type wifi ifname "$WIFI_IF" con-name bimmhacks-hotspot autoconnect yes ssid "$SSID" \
                802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared ipv6.method disabled \
                wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
        fi
        nmcli con up bimmhacks-hotspot >/dev/null 2>&1 || echo "WARNING: the hotspot did not start - is $WIFI_IF the Wi-Fi interface? (nmcli dev)" >&2
    fi
else
    echo "WARNING: nmcli not found - set the Ethernet port to link-local (169.254.x.x) and a Wi-Fi hotspot yourself" >&2
fi

# 4. the server, started with the box ---------------------------------------------------------------
PIN_ARG=""
[ -n "$PIN" ] && PIN_ARG=" --pin $PIN"
cat > /etc/systemd/system/bimmhacks-pocket.service <<EOF
[Unit]
Description=Bimmhacks Pocket (phone page for root access and the MGU App Store)
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 $SERVER --host 0.0.0.0 --port $PORT$PIN_ARG
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
chmod 600 /etc/systemd/system/bimmhacks-pocket.service       # the PIN, if any, is in it
systemctl daemon-reload
systemctl enable --now bimmhacks-pocket
sleep 2
systemctl is-active --quiet bimmhacks-pocket && echo "server: running" || { echo "server: NOT running - see: journalctl -u bimmhacks-pocket" >&2; exit 1; }

HOST=$(hostname)
echo
echo "Done. On the phone:"
if [ "$HOTSPOT" = 1 ]; then
    echo "  1. join Wi-Fi \"$SSID\" (password: $PASS)"
    echo "  2. open  http://10.42.0.1:$PORT/   (or http://$HOST.local:$PORT/)"
else
    echo "  open  http://$HOST.local:$PORT/  on the same Wi-Fi as this box"
fi
[ -n "$PIN" ] && echo "  3. enter the PIN when asked: $PIN"
echo "Plug the ENET cable into the car's OBD port and the box's Ethernet port; power the box from the car (USB)."
