# Hosted read-only irlpls MCP server (https://mcp.irlpls.com/mcp). Not built or deployed yet: no Docker on the dev
# machine; the same entrypoint is smoke-tested without Docker by deploy/mcp_smoke.py and deploy/smoke.sh.
# Build from the repo root:  docker build -f deploy/Dockerfile -t irlpls-mcp .
#
# Stage 1 builds the same wheels that go to PyPI; stage 2 installs only those wheels plus deploy/serve.py, so the image
# holds no repo files (no cards/, docs/, tests/, local data such as irlpls/data/health.json).
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml MANIFEST.in README.pypi.md LICENSE NOTICE LICENSE-DATA.md ./
COPY irlpls ./irlpls
COPY packages/irlpls-mcp ./packages/irlpls-mcp
RUN pip install --no-cache-dir build==1.2.2.post1 \
 && python -m build --wheel --outdir /wheels . \
 && python -m build --wheel --outdir /wheels packages/irlpls-mcp

FROM python:3.12-slim
# Read-only twice over: the env var and the --read-only flag below. serve.py also refuses to start without it.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 IRLPLS_MCP_READ_ONLY=1 \
    IRLPLS_CLIENT_IP_HEADER=fly-client-ip
COPY --from=build /wheels /wheels
RUN pip install /wheels/*.whl "mcp==2.3.0" && rm -rf /wheels \
 && useradd -r -u 10001 -M -s /usr/sbin/nologin app
WORKDIR /app
COPY deploy/serve.py ./serve.py
# Non-root, no home, no shell.
USER app
EXPOSE 8765
# Logging (the "logs nothing" promise): serve.py turns the uvicorn access log off (no client IP, path or status lines),
# sets uvicorn and the MCP SDK to WARNING, and never logs request or response bodies. It swaps the outbound geocoder
# for the bundled offline gazetteer, so the server makes no network calls of its own. Rate limits are in memory.
# --allow-remote is passed on purpose: irlpls-mcp refuses a non-loopback bind without it, because streamable-http has
# no authentication. That is acceptable here only because the server is --read-only (7 planning tools, no
# irlpls_submit, no protocol tools, no provider calls) and holds no credentials. Never drop --read-only from this line.
# 0.0.0.0 is inside the container only; the platform proxy terminates TLS in front of it.
CMD ["python", "serve.py", "--read-only", "--allow-remote", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8765"]
