#!/bin/bash
# Start the Barrier client, connecting to the specified server IP

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BARRIERC="$SCRIPT_DIR/build/bin/barrierc"

if [ ! -x "$BARRIERC" ]; then
    echo "Error: barrierc not found at $BARRIERC"
    echo "Run clean_build.sh first."
    exit 1
fi

SERVER_IP="${1:?Usage: $0 <server-ip> [port]}"
PORT="${2:-24800}"

echo "Connecting to barrier server at $SERVER_IP:$PORT ..."
exec "$BARRIERC" -f --debug INFO --disable-crypto --name "$(hostname -s)" "$SERVER_IP:$PORT"
