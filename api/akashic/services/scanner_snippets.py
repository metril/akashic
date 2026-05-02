"""Render ready-to-paste scanner snippets for a freshly-minted
join token. Five paste targets:

  shell        — bare `akashic-scanner claim …` invocation
  docker_run   — `docker run --rm -v scanner-data:/secrets …`
  compose      — full docker-compose service block
  k8s          — Job manifest that runs claim once + a Deployment
                 that runs the agent thereafter (mounting a Secret
                 the Job populates)
  env          — KEY=VALUE pairs for setups that template their own
                 runner

Server-side templating keeps formatting consistent across the UI's
five tabs and lets us version the rendering output cleanly.
"""
from __future__ import annotations

from akashic.protocol import PROTOCOL_VERSION

# Tag the published image with v0.3.x once the release pipeline cuts
# it; keep `latest` for the local-dev path until then.
IMAGE_REF = "ghcr.io/jagannath/akashic-scanner:latest"


def _shell(api_url: str, token: str) -> str:
    return (
        "akashic-scanner claim \\\n"
        f"  --api={api_url} \\\n"
        f"  --token={token}"
    )


def _docker_run(api_url: str, token: str) -> str:
    return (
        "docker run --rm \\\n"
        "  -v akashic-scanner-data:/secrets \\\n"
        f"  {IMAGE_REF} \\\n"
        f"  claim --api={api_url} --token={token} --start-after"
    )


def _compose(api_url: str, token: str, label: str) -> str:
    safe = label.replace(" ", "-").lower() or "scanner"
    return (
        "services:\n"
        f"  akashic-scanner-{safe}:\n"
        f"    image: {IMAGE_REF}\n"
        "    restart: unless-stopped\n"
        "    command:\n"
        "      - claim\n"
        f"      - --api={api_url}\n"
        f"      - --token={token}\n"
        "      - --start-after\n"
        "    volumes:\n"
        f"      - scanner-data-{safe}:/secrets\n"
        "\n"
        "volumes:\n"
        f"  scanner-data-{safe}:\n"
    )


def _k8s(api_url: str, token: str, label: str) -> str:
    safe = label.replace(" ", "-").lower() or "scanner"
    return (
        "# 1. Secret holds the claim token; the Job will consume it once\n"
        "#    and write the durable scanner key+id back into\n"
        f"#    a separate Secret named `akashic-{safe}-creds`.\n"
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        f"  name: akashic-{safe}-claim\n"
        "type: Opaque\n"
        "stringData:\n"
        f"  token: {token}\n"
        "---\n"
        "apiVersion: batch/v1\n"
        "kind: Job\n"
        "metadata:\n"
        f"  name: akashic-{safe}-claim\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      restartPolicy: OnFailure\n"
        "      containers:\n"
        f"        - name: claim\n"
        f"          image: {IMAGE_REF}\n"
        "          args:\n"
        "            - claim\n"
        f"            - --api={api_url}\n"
        "            - --token=$(CLAIM_TOKEN)\n"
        "          env:\n"
        "            - name: CLAIM_TOKEN\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        f"                  name: akashic-{safe}-claim\n"
        "                  key: token\n"
        "          volumeMounts:\n"
        "            - name: creds\n"
        "              mountPath: /secrets\n"
        "      volumes:\n"
        "        - name: creds\n"
        "          emptyDir: {}\n"
        "# 2. After the Job succeeds, run the agent against the same\n"
        "#    PVC. (Or rebuild the workflow with a single pod that\n"
        "#    chains both: `claim --start-after`.)\n"
    )


def _env(api_url: str, token: str) -> str:
    return (
        f"AKASHIC_API_URL={api_url}\n"
        f"AKASHIC_CLAIM_TOKEN={token}\n"
    )


def render_snippets(
    *, api_url: str, token: str, label: str,
) -> dict[str, str]:
    return {
        "shell": _shell(api_url, token),
        "docker_run": _docker_run(api_url, token),
        "compose": _compose(api_url, token, label),
        "k8s": _k8s(api_url, token, label),
        "env": _env(api_url, token),
    }


# Re-exported so the schema layer can document it without depending
# on the protocol module directly.
SERVER_PROTOCOL_VERSION = PROTOCOL_VERSION
