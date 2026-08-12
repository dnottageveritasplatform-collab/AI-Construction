# Veritas AI Construction Platform — production image for Render (Docker runtime).
# Render sets PORT; bind must be 0.0.0.0:$PORT.

FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# OpenMP runtime commonly required by ifcopenshell / scientific wheels on Debian.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 10000

# Persist uploads + data when Render mounts a disk at /var/data (see docker-entrypoint.sh).
# gthread: SSE (/api/events) holds a connection open; sync worker would block all other requests (502s).
CMD ["/app/docker-entrypoint.sh"]
