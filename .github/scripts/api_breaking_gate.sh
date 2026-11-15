#!/usr/bin/env bash
# Breaking-change gate for the public API contract (openapi.json).
#
# Compares the committed openapi.json with the one at the last release tag
# using oasdiff. Breaking changes (ERR level) fail the gate unless the top
# CHANGELOG.md section has a `### Breaking` subsection announcing them.
#
# "Last tag" = the highest-versioned stable `v*` tag reachable from HEAD that
# does not point at HEAD itself (pre-release tags are ignored). If no such
# tag exists, or it predates openapi.json, there is nothing to compare and the
# gate skips. It fails closed otherwise: a shallow clone, a missing tool, an
# unreadable spec or any oasdiff error fails the gate; only oasdiff's
# documented "breaking changes found" status (1) can be waived by the changelog.
#
# Usage: api_breaking_gate.sh [--base-spec FILE] [--head-spec FILE] [--changelog FILE]
#   --base-spec  compare against FILE instead of the last tag's spec (tests)
set -euo pipefail

# Pinned: bump deliberately. oasdiff reads OpenAPI 3.1 from v1.10 on.
OASDIFF_IMAGE="${OASDIFF_IMAGE:-tufin/oasdiff:v1.11.7@sha256:d6f1e645251cc1da527311955347d8ce8bd2b7ed09bcce07176b2577b8daf19c}"

base_spec="" head_spec="openapi.json" changelog="CHANGELOG.md"
while [ $# -gt 0 ]; do
  case "$1" in
    --base-spec) base_spec="$2"; shift 2 ;;
    --head-spec) head_spec="$2"; shift 2 ;;
    --changelog) changelog="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

[ -f "$head_spec" ] || { echo "::error::$head_spec not found"; exit 1; }
[ -f "$changelog" ] || { echo "::error::$changelog not found"; exit 1; }

if [ -z "$base_spec" ]; then
  if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
    echo "::error::shallow clone: cannot find the last release tag (checkout with fetch-depth: 0)"
    exit 1
  fi
  # git runs unguarded (set -e fails the gate on any git error); only grep's
  # "no match" status (1) is tolerated, via the explicit `|| [ $? -eq 1 ]`.
  tags="$(git -c versionsort.suffix=- tag --merged HEAD --list 'v[0-9]*' --sort=-version:refname)"
  at_head="$(git tag --points-at HEAD)"
  tag="$(printf '%s\n' "$tags" | { grep -Ev -- '-' || [ $? -eq 1 ]; } \
          | { grep -vxFf <(printf '%s\n' "$at_head") || [ $? -eq 1 ]; } | head -n1)"
  if [ -z "$tag" ]; then
    echo "No release tag reachable from HEAD; skipping the breaking-change gate."
    exit 0
  fi
  if [ -z "$(git ls-tree --name-only "$tag" -- openapi.json)" ]; then
    echo "Last release tag $tag has no openapi.json; skipping the breaking-change gate."
    exit 0
  fi
  git show "$tag:openapi.json" > "$tmp/base.json"
  echo "Comparing $head_spec against $tag"
else
  [ -f "$base_spec" ] || { echo "::error::$base_spec not found"; exit 1; }
  cp "$base_spec" "$tmp/base.json"
  echo "Comparing $head_spec against $base_spec"
fi
cp "$head_spec" "$tmp/head.json"

set +e
docker run --rm -v "$tmp:/specs:ro" "$OASDIFF_IMAGE" \
  breaking /specs/base.json /specs/head.json --fail-on ERR
rc=$?
set -e

case "$rc" in
  0)
    echo "No breaking API changes."
    exit 0 ;;
  1)
    # oasdiff's --fail-on status; anything else is a tool error.
    if awk '/^## v/ { n++; if (n == 2) exit } n == 1 { print }' "$changelog" | grep -q '^### Breaking'; then
      echo "Breaking API changes found and announced under '### Breaking' in the top CHANGELOG section."
      exit 0
    fi
    echo "::error::Breaking API changes found, but the top section of $changelog has no '### Breaking' subsection. Announce them there, or revert the change."
    exit 1 ;;
  *)
    echo "::error::oasdiff failed (exit $rc); treating as a failed gate, not as 'no breaking changes'"
    exit 1 ;;
esac
