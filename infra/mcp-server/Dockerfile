# The Postgres MCP server, packaged to be reachable from the Foundry Agent
# Service. See server.py for why this is built from source rather than using
# the published `crystaldba/postgres-mcp` image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Pinned so the image is reproducible. This commit is the first one on `main`
# that has both `--transport=streamable-http` and the `mcp[cli]<2` pin.
ARG POSTGRES_MCP_REF=15c8e33353546148acc2d8bd784551cf3905d1e2

RUN pip install --no-cache-dir \
      "postgres-mcp @ https://github.com/crystaldba/postgres-mcp/archive/${POSTGRES_MCP_REF}.tar.gz" \
      "psycopg[binary]>=3.2" \
      "uvicorn[standard]>=0.30,<1"

WORKDIR /app
COPY server.py ./

EXPOSE 8000
CMD ["python", "server.py"]
