"""Fail-closed check that every observed response is documented in the OpenAPI spec."""
from __future__ import annotations

import re
from typing import Iterable


def _template_for(spec: dict, method: str, route_path: str, concrete: str) -> str | None:
    """Full spec path for a matched route (its own `path` may lack the router prefix)."""
    for tpl, ops in spec["paths"].items():
        if method.lower() in ops and tpl.endswith(route_path):
            if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", tpl), concrete):
                return tpl
    return None


def audit_observed(spec: dict, observed: Iterable[tuple[str, str, str, int, bool]]) -> list[str]:
    """Problems for (method, route path, concrete path, status, in_schema) observations.

    Only routes excluded from the schema (include_in_schema=False) are skipped.
    A route in the schema with no matching spec operation is a problem, and a
    status counts as documented only when listed explicitly (``default`` is not
    accepted).
    """
    problems: list[str] = []
    for method, route_path, concrete, status, in_schema in sorted(observed):
        if not in_schema:
            continue
        tpl = _template_for(spec, method, route_path, concrete)
        if tpl is None:
            problems.append(f"{method} {concrete} -> {status} (route {route_path} has no spec operation)")
            continue
        responses = spec["paths"][tpl][method.lower()]["responses"]
        if str(status) not in responses:
            problems.append(f"{method} {tpl} -> {status} (documented: {sorted(responses)})")
    return sorted(set(problems))
