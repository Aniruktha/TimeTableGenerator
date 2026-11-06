# Timetable -- one container: the web app, the solver, and its SQLite database.
#   docker build -t timetable .
#   docker run -p 8000:8000 -v timetable-data:/var/lib/timetable timetable
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Dependencies first, so code changes do not reinstall OR-Tools.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY data/amcs_sem2.yaml data/tiny_demo.yaml ./data/
RUN uv sync --locked --no-dev

RUN useradd --system --uid 10001 --home /var/lib/timetable timetable \
 && mkdir -p /var/lib/timetable && chown timetable /var/lib/timetable
USER timetable

# Everything that must survive a restart or an upgrade lives here.
ENV TTGEN_HOME=/var/lib/timetable \
    TTGEN_DATA_DIR=/app/data
VOLUME ["/var/lib/timetable"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["tt", "serve", "--host", "0.0.0.0", "--port", "8000", "--proxy"]
