FROM python:3.12.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/howreliable \
    && /opt/howreliable/bin/python -m pip install .

FROM python:3.12.11-slim-bookworm AS runtime

ENV PATH="/opt/howreliable/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 howreliable \
    && useradd --uid 10001 --gid howreliable --no-create-home --shell /usr/sbin/nologin howreliable

COPY --from=builder /opt/howreliable /opt/howreliable
WORKDIR /app

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "howreliable.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
