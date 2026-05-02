# Standalone scanner-agent image. Identical Go build to api/Dockerfile's
# scanner-builder stage, but ships the binary on its own (Phase 2 of
# the multi-scanner work — the api image stops bundling the binary in
# Phase 3 once subprocess-spawn is removed).
FROM golang:1.23-alpine AS build
WORKDIR /src
# go.mod's `replace` directive for internal/vendor/go-smb2 means we
# need the full source tree before `go mod download` (the replace target
# has to exist on disk). Same compromise as the api image.
COPY scanner/ ./scanner/
ARG VERSION=dev
RUN cd scanner && go mod download \
    && CGO_ENABLED=0 go build \
        -trimpath \
        -ldflags "-s -w -X main.Version=${VERSION}" \
        -o /out/akashic-scanner ./cmd/akashic-scanner

FROM alpine:3.20
# Non-root by default; the agent only needs to read its own key file
# and reach the api over the network.
RUN adduser -D -u 1000 scanner
# Pre-create /secrets owned by the scanner user. When a docker
# named volume is mounted here on first launch, docker copies the
# directory's ownership (1000:1000) into the fresh volume — so the
# `auto`/`claim`/`discover` paths can write the keypair without
# host-side chown ceremony.
RUN mkdir -p /secrets && chown scanner:scanner /secrets
USER scanner
COPY --from=build /out/akashic-scanner /usr/local/bin/akashic-scanner
ENTRYPOINT ["/usr/local/bin/akashic-scanner"]
