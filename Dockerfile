# Dhaka Traffic Sim: one image with the FastAPI/SUMO backend serving the built frontend.
#
#   docker build -t traffic-sim .
#   docker run -p 8000:8000 -v traffic-sim-areas:/app/backend/data/areas traffic-sim
#
# Road networks already built locally (backend/data/areas) are baked into the image.
# Any area listed in a region pack that is missing is built on first start (downloads OSM, see
# docker/entrypoint.sh), so a fresh clone works too.

# --- frontend -------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- python environment ---------------------------------------------------
FROM python:3.12-slim-bookworm AS venv
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
# The SUMO wheel ships GUIs, a C++ SDK and a tools tree the server never uses
# (sumolib/traci come from their own packages); keep only sumo and netconvert.
RUN cd .venv/lib/python3.12/site-packages/sumo \
    && find bin -type f ! -name sumo ! -name netconvert -delete \
    && rm -rf tools include lib64 cmake

# --- runtime --------------------------------------------------------------
FROM python:3.12-slim-bookworm AS app
# The sumo/netconvert binaries link X11/GL even when run headless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libx11-6 libxext6 libxrender1 libgl1 libatomic1 libexpat1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --home-dir /app --no-create-home app

ENV PYTHONUNBUFFERED=1 \
    PATH=/app/backend/.venv/bin:$PATH
WORKDIR /app/backend

COPY --from=venv /usr/local/bin/uv /usr/local/bin/uv
COPY --from=venv /app/backend/.venv ./.venv
COPY --chown=app:app backend/ ./
RUN UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --inexact \
    && rm /usr/local/bin/uv \
    && mkdir -p data/areas && chown app:app data data/areas
COPY --from=frontend /app/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8000
VOLUME /app/backend/data/areas
ENV TRAFFIC_SIM_MAX_SIMS=4 \
    PREPARE_AREAS=missing

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/areas', timeout=4)"

ENTRYPOINT ["entrypoint.sh"]
CMD ["traffic-sim-server", "--host", "0.0.0.0", "--port", "8000"]
