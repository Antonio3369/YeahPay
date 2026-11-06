#!/usr/bin/env python3
"""Minimal HTTP smoke test for a deployed backend."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def request(url: str, token: str | None = None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    checks = [("/health", False), ("/api/v1/auth/me", True)]
    for path, needs_auth in checks:
        if needs_auth and not args.token:
            print(f"SKIP {path}: 未提供 --token")
            continue
        try:
            status, payload = request(base + path, args.token if needs_auth else None)
            print(f"OK {status} {path}: {payload}")
        except (urllib.error.URLError, ValueError) as exc:
            print(f"FAIL {path}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
