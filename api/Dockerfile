# ---- builder: compile the akashic-scanner Go binary ----
# Alpine base avoids the bulk of the CVE surface in the full Debian-based
# golang image. We build with CGO_ENABLED=0 so the resulting binary has no
# runtime dependency on the builder image at all.
FROM golang:1.23-alpine AS scanner-builder
RUN apk add --no-cache git
WORKDIR /src
# Note: the scanner's go.mod has a `replace` directive pointing at
# internal/vendor/go-smb2, so `go mod download` requires the local replace
# target to exist on disk — meaning we have to copy the full source tree
# *before* downloading deps. This costs us layer-cache friendliness: any
# scanner edit (even one line) re-downloads all Go modules. TODO: when the
# vendored go-smb2 patch lands upstream and the replace can be dropped, split
# this into the standard "COPY go.mod go.sum → go mod download → COPY ." form.
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
