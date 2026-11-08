#!/bin/sh
# fake_ssh.sh - stands in for the OpenSSH client in tests: answers the remote commands the root tool
# runs on a head unit, without any car. The command is the last argument, as OpenSSH takes it.
# Installed as "ssh" at the front of PATH by the tests only; a real ssh is never replaced.
for a; do cmd="$a"; done
case "$cmd" in
    id) echo "uid=0(root) gid=0(root) groups=0(root)" ;;
    "id -u") echo 0 ;;
    "cat > "*) cat > /dev/null ;;
    "sh "*) echo "sshd-always-on: ok ($cmd)" ;;
    *) echo "fake-ssh: $cmd" ;;
esac
exit 0
