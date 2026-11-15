"""Write the API's OpenAPI spec to ``openapi.json`` at the repo root.

Deterministic (sorted keys, stable indent) and needs no DB, Redis or
lifespan: it only builds the app and calls ``app.openapi()``.

    python api/scripts/dump_openapi.py [output-path]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "api"))
# Settings generates a secret key file on import; keep it out of /data.
os.environ.setdefault("SECRET_KEY_FILE", os.path.join(tempfile.mkdtemp(prefix="akashic-dump-"), "secret.key"))


def render() -> str:
    from akashic.main import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "openapi.json"
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
