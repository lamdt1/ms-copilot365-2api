# Base image: Python 3.11 Bookworm (slim)
FROM python:3.11-slim-bookworm

# Install system dependencies for Firefox/Camoufox, Xvfb, VNC, noVNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    curl \
    wget \
    bzip2 \
    procps \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libx11-xcb1 \
    libasound2 \
    fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fetch Camoufox browser binary + GeoLite2 MMDB (upstream URL is broken, use helper)
COPY fetch_camoufox.sh /tmp/fetch_camoufox.sh
RUN chmod +x /tmp/fetch_camoufox.sh && /tmp/fetch_camoufox.sh

# Copy application source
COPY . .

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

# Create data volume directory
RUN mkdir -p /app/data

# Expose ports: 8000 (FastAPI), 6080 (noVNC)
EXPOSE 8000 6080

# Entrypoint: starts Xvfb + Fluxbox + x11vnc + noVNC + FastAPI
ENTRYPOINT ["/app/docker-entrypoint.sh"]
