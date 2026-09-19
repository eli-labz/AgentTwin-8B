# Reference image for the AgentTwin Enterprise control plane.
# Cloud-neutral: no provider SDKs, no baked secrets, no Harbor/LiteLLM.
# Build from the repository root:
#   docker build -f deploy/enterprise/Dockerfile -t agenttwin-enterprise-api:local .
#
# The image serves /api/v1 + /console. It does not change `matraix run`.

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    MATRIX_ENTERPRISE_STORE=sqlite \
    MATRIX_ENTERPRISE_DB=/data/enterprise.sqlite

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data

# Slim runtime: FastAPI stack only. Do not `pip install -e .` (that pulls LiteLLM).
RUN pip install --no-cache-dir \
    "fastapi>=0.128.0" \
    "uvicorn>=0.38.0" \
    "pydantic>=2.11.7" \
    "pyyaml>=6.0.2"

COPY --chown=app:app src/matraix /app/src/matraix
COPY --chown=app:app persona/schema /app/persona/schema

USER app
EXPOSE 8090

# Tokens and OIDC secrets come from the environment / a mounted Secret.
# Never bake MATRIX_ENTERPRISE_API_TOKEN into this image.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health')"

CMD ["python", "-m", "uvicorn", "matraix.enterprise.api:app", "--host", "0.0.0.0", "--port", "8090"]
