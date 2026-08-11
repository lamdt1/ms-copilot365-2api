#!/bin/bash
set -e

# 1. Start Virtual Frame Buffer (Xvfb)
echo "Starting Xvfb on display ${DISPLAY:-:99}..."
Xvfb ${DISPLAY:-:99} -screen 0 1280x800x24 &
sleep 2

# 2. Start Fluxbox Window Manager (required to keep display active and window placements)
echo "Starting Fluxbox..."
fluxbox &
sleep 1

# 3. Start VNC Server (x11vnc)
echo "Starting x11vnc..."
if [ -n "$VNC_PASSWORD" ]; then
  # Create password file
  mkdir -p ~/.vnc
  x11vnc -storepw "$VNC_PASSWORD" ~/.vnc/passwd
  x11vnc -display ${DISPLAY:-:99} -rfbport 5900 -rfbauth ~/.vnc/passwd -forever -quiet &
else
  x11vnc -display ${DISPLAY:-:99} -rfbport 5900 -nopw -forever -quiet &
fi
sleep 1

# 4. Start noVNC Websocket Bridge
if [ "${NOVNC_ENABLE:-true}" = "true" ]; then
  echo "Starting noVNC websockify bridge on port 6080 -> 5900..."
  websockify --web /usr/share/novnc 6080 localhost:5900 &
fi
sleep 1

# 5. Start FastAPI Proxy Application
echo "Starting FastAPI Application on ${HOST:-0.0.0.0}:${PORT:-8000}..."
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
