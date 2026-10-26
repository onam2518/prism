#!/usr/bin/env python3
"""Fail-closed JSON checks used by the Fly deploy workflow.

The workflow deliberately treats malformed or incomplete responses as unknown;
the caller decides how many transient failures are safe to retry.
"""

import json
import sys


def ingesting_state(payload):
    """Return ``idle``, ``busy``, or ``unknown`` for a /config response."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return "unknown"
    if not isinstance(data, dict) or type(data.get("ingesting")) is not bool:
        return "unknown"
    return "busy" if data["ingesting"] else "idle"


def deployment_ready(payload, expected_build):
    """Accept only the exact non-mock target build and configured backend."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("build") == expected_build
        and data.get("forcedMock") is False
        and data.get("backend") == "supabase"
        and data.get("configured") is True
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("ingesting", "deployment"):
        return 2
    payload = sys.stdin.read()
    if argv[0] == "ingesting":
        print(ingesting_state(payload))
        return 0
    if len(argv) != 2:
        return 2
    return 0 if deployment_ready(payload, argv[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
