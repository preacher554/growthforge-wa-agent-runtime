#!/bin/bash
# Kill anything on port 3300 before starting
fuser -k 3300/tcp 2>/dev/null || true
sleep 2

# Extra kill for stubborn processes
PID=$(ss -tlnp 2>/dev/null | grep '3300' | grep -oP 'pid=\K[0-9]+')
if [ -n "$PID" ]; then
    kill -9 "$PID" 2>/dev/null || true
    sleep 1
fi

# Verify port is free
if ss -tlnp 2>/dev/null | grep -q '3300'; then
    echo "ERROR: Port 3300 still occupied"
    exit 1
fi

echo "Port 3300 clear, starting uvicorn..."
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 3300 --log-level info
