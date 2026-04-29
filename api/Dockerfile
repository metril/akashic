# ---- builder: compile the akashic-scanner Go binary ----
FROM golang:1.23 AS scanner-builder
WORKDIR /src
# Scanner has a `replace` pointing at internal/vendor/go-smb2, so we need the
# full source tree before `go mod download` can resolve dependencies.
COPY scanner/ ./scanner/
RUN cd scanner && go mod download \
    && CGO_ENABLED=0 go build -o /out/akashic-scanner ./cmd/akashic-scanner

# ---- runtime: python + bundled scanner ----
FROM python:3.12-slim

WORKDIR /app

COPY api/pyproject.toml .
COPY api/akashic/ akashic/
COPY api/alembic.ini .
COPY api/alembic/ alembic/

RUN apt-get update && apt-get install -y gcc libldap2-dev libsasl2-dev && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir .

# The scanner binary lives on PATH so api code can spawn it via shutil.which.
COPY --from=scanner-builder /out/akashic-scanner /usr/local/bin/akashic-scanner

CMD ["uvicorn", "akashic.main:app", "--host", "0.0.0.0", "--port", "8000"]
