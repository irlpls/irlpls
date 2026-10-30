# Hosted read-only irlpls MCP server. DRAFT: not built or deployed (no Docker on the dev machine).
# Build from the repo root:  docker build -f deploy/Dockerfile -t irlpls-mcp .
FROM python:3.12-slim
# Read-only by default, twice over: the env var and the --read-only flag below. serve.py also refuses to start without it.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 IRLPLS_MCP_READ_ONLY=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY irlpls ./irlpls
COPY deploy/serve.py ./deploy/serve.py
RUN pip install ".[mcp]" && useradd -r -u 10001 -M -s /usr/sbin/nologin app
# Non-root, no home, no shell.
USER app
EXPOSE 8765
# Logging (the "logs nothing" promise): serve.py turns the uvicorn access log off (no client IP, path or status lines),
# sets uvicorn and the MCP SDK to WARNING, and never logs request or response bodies. It also disables the outbound
# geocoder, so the server makes no network calls of its own. See deploy/RUNBOOK.md.
# --allow-remote is passed on purpose: irlpls-mcp refuses a non-loopback bind without it, because streamable-http has
# no authentication. That is acceptable here only because the server is --read-only (7 planning tools, no
# irlpls_submit, no protocol tools, no provider calls) and holds no credentials. Never drop --read-only from this line.
# 0.0.0.0 is inside the container only; the platform proxy terminates TLS in front of it.
CMD ["python", "deploy/serve.py", "--read-only", "--allow-remote", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8765"]
