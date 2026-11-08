#!/bin/sh
# Bimmhacks iDrive 7 App Installer - macOS / Linux launcher.
#   ./install.sh                         opens the installer window
#   ./install.sh install --key id_rsa    command line (see: ./install.sh --help)
HERE=$(cd "$(dirname "$0")" && pwd)
PY=
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 6))' 2>/dev/null; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
    echo "Python 3 is needed. macOS: https://www.python.org/downloads/ (or: brew install python)"
    echo "Linux: sudo apt install python3 python3-tk"
    exit 1
fi
if ! command -v node >/dev/null 2>&1; then
    echo "Node.js 14+ is needed: https://nodejs.org/  (macOS: brew install node; Ubuntu 24.04: sudo apt install nodejs)"
    exit 1
fi
if [ $# -eq 0 ] && ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "This Python has no Tk window toolkit (Linux: sudo apt install python3-tk)."
    echo "Command line instead:  ./install.sh install --key /path/to/id_rsa [--ip 169.254.36.47]"
    exit 1
fi
exec "$PY" "$HERE/installer/bimmhacks_installer.py" "$@"
