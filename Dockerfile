# ─────────────────────────────────────────────────────────────────────────────
# OmniStream AI — Python Backend Dockerfile
# Base: Python 3.11 Slim (Debian Bookworm)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# ─── System Dependencies ───────────────────────────────────────────────────
# ffmpeg: Required for audio extraction and video merging via yt-dlp
# curl: Health check utility
# ca-certificates: Ensure SSL cert validation works
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# ─── Create non-root user for security ────────────────────────────────────
RUN groupadd --gid 1001 omnistream \
    && useradd --uid 1001 --gid omnistream --shell /bin/bash --create-home omnistream

# ─── Working Directory ─────────────────────────────────────────────────────
WORKDIR /app

# ─── Copy requirements first (layer caching) ──────────────────────────────
COPY requirements.txt ./

# ─── Install Python Dependencies ──────────────────────────────────────────
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ─── Copy Application Code ────────────────────────────────────────────────
COPY app.py ./

# ─── Temporary directory for downloads (writable by app user) ─────────────
RUN mkdir -p /tmp/omnistream && chown omnistream:omnistream /tmp/omnistream

# ─── Change ownership of app directory ────────────────────────────────────
RUN chown -R omnistream:omnistream /app

# ─── Switch to non-root user ──────────────────────────────────────────────
USER omnistream

# ─── Expose Port ──────────────────────────────────────────────────────────
EXPOSE 8000

# ─── Health Check ─────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# ─── Run with Gunicorn ────────────────────────────────────────────────────
# workers: 2 * CPU cores + 1 (use 2 for Render free tier)
# timeout: 300s to allow large video downloads to complete
# worker-class: sync (streaming responses work better with sync workers)
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--worker-class", "sync", \
     "--timeout", "300", \
     "--keep-alive", "5", \
     "--max-requests", "500", \
     "--max-requests-jitter", "50", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "app:app"]
