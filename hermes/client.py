"""Small authenticated client for the local policy API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.environ.get("API_URL", "http://127.0.0.1:8080")).rstrip("/")
        self.token = token or os.environ.get("API_TOKEN", "")
        if not self.token:
            raise ApiError("API_TOKEN is not configured")

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "contribute-hermes/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise ApiError(f"{method} {path}: HTTP {exc.code}: {detail}") from exc

    def get(self, path: str, **query: object) -> Any:
        suffix = "?" + urllib.parse.urlencode(query) if query else ""
        return self.request("GET", path + suffix)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, body)
