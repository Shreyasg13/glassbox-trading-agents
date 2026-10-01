# GlassBox backend — FastAPI gateway over the multi-agent-trading-system engine.
# Data artifacts (reports/, database/) are mounted at runtime from backend-source,
# NOT baked into this image — see docker-compose.yml volumes.
#
# Two stages: dependencies are installed in a "builder" that has a compiler, then
# only the installed packages are copied into a clean runtime image. Every
# dependency ships prebuilt wheels, so the ~320 MB build-essential toolchain the
# old single-stage image carried was pure dead weight at runtime.

FROM python:3.14-slim AS builder
WORKDIR /build
# Only needed if a dependency ever lacks a wheel for this platform.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# gunicorn is pinned in requirements.txt with everything else, so a rebuild can
# never silently pull in a different major version.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim AS runtime
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# 2 workers is a sane default for a single small VM; bump via WEB_CONCURRENCY.
#
# --forwarded-allow-ips="*": this container is only reachable from Caddy over
# the compose network (docker-compose.yml `expose`s 8000, it never publishes
# it), and Caddy replaces any client-supplied X-Forwarded-For with the real
# client address. Trusting it is what makes request.client.host the actual
# visitor's IP instead of Caddy's -- without it every visitor shares ONE
# rate-limit bucket (see app/rate_limit.py). Do NOT publish port 8000 to the
# host while this is set.
CMD ["sh", "-c", "python -m app.migrate || echo 'WARNING: database migration failed; starting anyway'; gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8000 --forwarded-allow-ips='*' --access-logfile -"]
