#!/bin/sh
# probe-car.sh - why can (or can't) a phone tool reach the BeeM-Flash bridge?
# Run as ROOT on the head unit. It sends NOTHING to the car. Read-only except a
# throwaway test listener (port 47001) and, in phase B only, one temporary
# firewall rule tagged "beem-probe" - both removed before it exits.
#
#   P2  network: Wi-Fi interfaces, routes, the firewall's input chain (complete)
#   P2b is the app's bridge actually listening (TCP 6801/13400, UDP 6811/13400)?
#   P3  can a PHONE reach the head unit on each Wi-Fi interface?
#       Phase A: as the firewall is now.  Phase B: with a temporary accept rule.
#       On the phone you only open an address in Safari / Chrome.
#
# Usage:  sh probe-car.sh              (P3 phases: 45 s each)
#         SECS=60 sh probe-car.sh
set -u
TPORT=47001
SECS="${SECS:-45}"
echo "=================================================================="
echo " BeeM-Flash bridge probe  -  sends nothing to the car"
echo "=================================================================="

echo; echo "### P2: network ###"
echo "--- Wi-Fi interfaces with an address ---"
WIFIS=""
for IF in $(ls /sys/class/net 2>/dev/null); do
  case "$IF" in wl*) ;; *) continue ;; esac
  A="$(ip -4 -o addr show "$IF" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
  [ -n "$A" ] || continue
  if [ "$IF" = "wlan_bridge" ]; then ROLE="the car's own Wi-Fi (DIRECT-BMW / CarPlay)"; else ROLE="car joined another network (e.g. your phone's hotspot)"; fi
  echo "  $IF  $A   $ROLE"
  WIFIS="$WIFIS $IF:$A"
done
[ -n "$WIFIS" ] || echo "  (none - join the car to Wi-Fi or connect the phone to the car first)"
echo "--- routes ---"; ip route 2>/dev/null | grep -v " via " ; echo "  (+ $(ip route 2>/dev/null | grep -c ' via ') routed hosts not shown)"

# the chain that has "hook input"
CH=""
for T in "ip filter input" "inet filter input" "ip filter INPUT"; do
  if nft list chain $T 2>/dev/null | grep -q "hook input"; then CH="$T"; break; fi
done
echo "--- firewall input chain (complete) ---"
if [ -n "$CH" ]; then
  nft list chain $CH
  for J in $(nft list chain $CH 2>/dev/null | sed -n 's/.*jump \([A-Za-z0-9_]*\).*/\1/p'); do
    echo "--- jumped-to chain: $J ---"
    nft list chain $(echo "$CH" | cut -d' ' -f1-2) "$J" 2>/dev/null
  done
else
  iptables -S INPUT 2>/dev/null || echo "(no readable nft input chain or iptables)"
fi

echo; echo "### P2b: is the BeeM-Flash bridge listening? (turn BRIDGE: ON first) ###"
L="$(netstat -ltnup 2>/dev/null || ss -ltnup 2>/dev/null)"
for P in "tcp 6801 HSFZ/ENET (MHD, E-Sys, ISTA)" "tcp 13400 DoIP" "udp 6811 HSFZ discovery" "udp 13400 DoIP discovery"; do
  set -- $P; PROTO=$1; PN=$2; shift 2
  if echo "$L" | grep -E "^$PROTO" | grep -qE "[:.]$PN[[:space:]]"; then echo "  $PROTO $PN  listening   - $*"
  else echo "  $PROTO $PN  NOT listening - $*"; fi
done
echo "  (if 6801 is NOT listening: update BeeM-Flash to v2.30.0+ and turn the bridge on)"

echo; echo "### P3: can the phone reach the head unit? ###"
if [ -z "$WIFIS" ]; then
  echo "No Wi-Fi interface has an address - skipping."
else
  echo "Put the PHONE on the same Wi-Fi as the head unit, then open in Safari / Chrome:"
  for W in $WIFIS; do echo "      http://${W#*:}:$TPORT/      (via ${W%%:*})"; done
  echo "A page saying 'BeeM-Flash probe: reached' means that path works."
  node -e "
    var n=require('net'),ifs=process.argv.slice(1);
    ifs.forEach(function(w){var ip=w.split(':')[1],name=w.split(':')[0];
      n.createServer(function(s){console.log('  >>> REACHED via '+name+' from '+String(s.remoteAddress).replace('::ffff:',''));
        s.end('HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\nBeeM-Flash probe: reached the head unit via '+name+'\n');})
      .on('error',function(e){console.log('  (cannot listen on '+ip+':$TPORT: '+e.code+')');}).listen($TPORT,ip);});
    setTimeout(function(){process.exit(0);},(2*$SECS+5)*1000);" $WIFIS &
  NPID=$!
  echo; echo "Phase A ($SECS s): the firewall as it is now. Open the address(es) on the phone now."
  sleep "$SECS"
  ADDED=""
  if [ -n "$CH" ]; then
    for W in $WIFIS; do
      nft insert rule $CH iifname "\"${W%%:*}\"" tcp dport $TPORT accept comment '"beem-probe"' 2>/dev/null && ADDED=1
    done
  fi
  if [ -n "$ADDED" ]; then
    echo; echo "Phase B ($SECS s): a TEMPORARY accept rule for port $TPORT is in place. Reload the page(s) now."
  else
    echo; echo "Phase B ($SECS s): no firewall rule could be added; reload anyway."
  fi
  sleep "$SECS"
  kill "$NPID" 2>/dev/null
  if [ -n "$CH" ]; then
    for H in $(nft -a list chain $CH 2>/dev/null | grep '"beem-probe"' | sed -n 's/.*# handle \([0-9]*\).*/\1/p'); do
      nft delete rule $CH handle "$H" 2>/dev/null
    done
    nft list chain $CH 2>/dev/null | grep -q '"beem-probe"' && echo "WARNING: a beem-probe rule is still present" || echo "(temporary rule removed)"
  fi
fi

echo; echo "=================================================================="
echo " READING THE RESULT"
echo "  REACHED in phase A     -> nothing blocks that path. If a tool still fails, the"
echo "                            app must be listening on 6801/13400 (see P2b)."
echo "  REACHED only in phase B -> the firewall blocks it: install the helper with"
echo "                            --open-iface <that interface> (e.g. wlan_bridge)."
echo "  never REACHED          -> the Wi-Fi isolates devices (or the phone is on another"
echo "                            network): try the other path (phone hotspot <-> car Wi-Fi)."
echo "=================================================================="
