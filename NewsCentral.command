#!/bin/bash
# Double-click to start News Central. Keep the window open while reading; close it to stop.
cd "$(dirname "$0")" || exit 1
PORT=8787

if curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/status"; then
  echo "News Central is already running."
  open "http://127.0.0.1:$PORT"
  exit 0
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
  echo "News Central needs Python 3.8 or newer."
  echo "Install Apple's command line tools with:  xcode-select --install"
  echo "or install Python from https://www.python.org/downloads/macos/"
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

exec python3 server.py --port "$PORT" --open "$@"
