#!/bin/sh
# fake_ssh.sh - stands in for the OpenSSH client in tests: answers the remote commands the root tool
# and the installer run on a head unit, without any car. The command is the last argument, as OpenSSH
# takes it. Installed as "ssh" at the front of PATH by the tests only; a real ssh is never replaced.
#
# Knobs (environment): FAKE_SSH_NO_STORE=1   the Store is not installed on this "unit"
#                      FAKE_SSH_SOURCE=added|already|waiting|error   what the add-source script "answers"
#                      FAKE_SSH_LOG=<file>    every command is appended there, for assertions
#                      FAKE_SSH_REAL_NODE=1   run the add-source script for real with the local node
#                                             (the test sets MGU_UPDATER_SOCKET / MGU_OAP_ROOT)
#                      FAKE_SSH_SOURCE_FILE=<file>  one answer per add-source call (first line, then removed)
# Restart of the head unit: FAKE_SSH_STATE=<file> holds "down:N" after a restart request; the next N
# `echo HU_UP` probes fail (ssh exit 255), then the unit answers again.
#                      FAKE_SSH_RESTART=ok|busy|error|missing   how the restart request is answered
#                      FAKE_SSH_DOWN_PROBES=<n>   probes that fail after a restart (default 2)
#                      FAKE_SSH_NEVER_DOWN=1      the unit ignores the restart request
#                      FAKE_SSH_NEVER_BACK=1      the unit goes down and stays down
for a; do cmd="$a"; done
cmd=${cmd#export LC_ALL=C; }          # the installer's Car prefixes its commands with this
[ -n "$FAKE_SSH_LOG" ] && printf '%s\n' "$cmd" >> "$FAKE_SSH_LOG"
case "$cmd" in
    id) echo "uid=0(root) gid=0(root) groups=0(root)" ;;
    "id -u") echo 0 ;;
    "cat > "*) cat > /dev/null ;;
    "sh "*) echo "sshd-always-on: ok ($cmd)" ;;
    "test -f /var/opt/mgu-deploy/mgu-deploy.sh"*)
        [ "${FAKE_SSH_NO_STORE:-0}" = 1 ] || echo STORE_PRESENT ;;
    "L=/var/opt/mgu-deploy/lock;"*)
        case "${FAKE_SSH_RESTART:-ok}" in
            busy) echo INSTALL_RUNNING; exit 3 ;;
            error) echo "[Error. ProxyLifecycleControl not available!]" ;;
            missing) echo "sh: 1: /usr/bin/nsm_control: not found"; exit 127 ;;
            *) if [ -n "$FAKE_SSH_STATE" ] && [ "${FAKE_SSH_NEVER_DOWN:-0}" != 1 ]; then
                   echo "down:${FAKE_SSH_DOWN_PROBES:-2}" > "$FAKE_SSH_STATE"
               fi ;;
        esac ;;
    "echo HU_UP")
        if [ -n "$FAKE_SSH_STATE" ] && [ -f "$FAKE_SSH_STATE" ]; then
            st=$(cat "$FAKE_SSH_STATE")
            n=${st#down:}
            if [ "${FAKE_SSH_NEVER_BACK:-0}" = 1 ]; then
                echo "ssh: connect to host 127.0.0.1 port 22: Connection refused" >&2; exit 255
            elif [ "$n" -gt 0 ]; then
                echo "down:$((n - 1))" > "$FAKE_SSH_STATE"
                echo "ssh: connect to host 127.0.0.1 port 22: Connection refused" >&2; exit 255
            fi
            rm -f "$FAKE_SSH_STATE"
        fi
        echo HU_UP ;;
    "/usr/bin/node - "*)
        if [ "${FAKE_SSH_REAL_NODE:-0}" = 1 ]; then
            # run the script for real, with this machine's node, against whatever socket the test points
            # MGU_UPDATER_SOCKET / MGU_OAP_ROOT at (the unit's node is /usr/bin/node; here it is on PATH)
            [ -n "$FAKE_SSH_LOG" ] && printf 'add-source-real %s\n' "$cmd" >> "$FAKE_SSH_LOG"
            cmd="node ${cmd#/usr/bin/node}"
            eval "$cmd"
            exit $?
        fi
        # the add-source script arrives on stdin and would run with the unit's node; answer for it
        script=$(cat)
        set -- $cmd
        repo=$3; chan=$4
        [ -n "$FAKE_SSH_LOG" ] && printf 'add-source %s %s script-bytes=%s\n' "$repo" "$chan" "$(printf %s "$script" | wc -c | tr -d ' ')" >> "$FAKE_SSH_LOG"
        mode=${FAKE_SSH_SOURCE:-added}
        if [ -n "$FAKE_SSH_SOURCE_FILE" ] && [ -s "$FAKE_SSH_SOURCE_FILE" ]; then
            mode=$(head -n 1 "$FAKE_SSH_SOURCE_FILE")
            tail -n +2 "$FAKE_SSH_SOURCE_FILE" > "$FAKE_SSH_SOURCE_FILE.tmp" && mv "$FAKE_SSH_SOURCE_FILE.tmp" "$FAKE_SSH_SOURCE_FILE"
        fi
        case "$mode" in
            already) echo "ALREADY $repo" ;;
            waiting) echo "WAITING: the updater is not running (ENOENT)"; exit 3 ;;
            error) echo "ERROR: the updater refused the secret (forbidden)"; exit 1 ;;
            *) echo "waiting for the updater: the updater is not running (ENOENT)"; echo "ADDED $repo ($chan)" ;;
        esac ;;
    *) echo "fake-ssh: $cmd" ;;
esac
exit 0
