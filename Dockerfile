# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DISPLAY=:99 DATA_DIR=/data CHROMIUM_EXECUTABLE=/usr/bin/chromium HEADLESS=0
RUN apt-get update && apt-get install -y --no-install-recommends chromium xvfb x11vnc novnc websockify fluxbox supervisor fonts-noto-cjk tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements.txt; else pip install --no-cache-dir -r requirements.txt; fi
RUN useradd --create-home --uid 1000 organizer && mkdir -p /data /tmp/supervisor && chown organizer:organizer /data /tmp/supervisor
COPY --chown=organizer:organizer app ./app
COPY --chown=organizer:organizer static ./static
COPY --chown=organizer:organizer supervisord.conf /etc/supervisor/conf.d/organizer.conf
USER organizer
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/organizer.conf"]
