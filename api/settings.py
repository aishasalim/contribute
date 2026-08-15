"""Environment-backed settings shared by API and background workers."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("DATABASE_URL", "")
    api_token: str = os.environ.get("API_TOKEN", "")
    cors_origins: tuple[str, ...] = tuple(
        value.strip()
        for value in os.environ.get("CORS_ORIGINS", "http://127.0.0.1:8000").split(",")
        if value.strip()
    )
    dashboard_url: str = os.environ.get(
        "DASHBOARD_URL", "https://contribute-drab.vercel.app/radar.html"
    )
    review_base_url: str = os.environ.get(
        "REVIEW_BASE_URL", "http://127.0.0.1:8080"
    ).rstrip("/")
    hermes_binary: str = os.environ.get("HERMES_BINARY", "hermes")
    hermes_discord_target: str = os.environ.get("HERMES_DISCORD_TARGET", "")
    allowed_seasons: tuple[str, ...] = tuple(
        value.strip()
        for value in os.environ.get(
            "ALLOWED_SEASONS", "fall-2026,winter-2027,spring-2027,summer-2027"
        ).split(",")
        if value.strip()
    )
    max_age_days: int = int(os.environ.get("MAX_AGE_DAYS", "14"))
    company_daily_limit: int = int(os.environ.get("COMPANY_DAILY_LIMIT", "3"))
    # Hosts the browser worker can actually drive. A role the worker cannot
    # finish should never be claimed: it escalates, returns to the queue, and
    # escalates again on the next batch. Extend this as adapters are added —
    # it must stay in step with hermes/adapters/__init__.py.
    applyable_hosts: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.environ.get(
            "APPLYABLE_ATS_HOSTS", "greenhouse.io,greenhouse.com,lever.co,ashbyhq.com"
        ).split(",")
        if value.strip()
    )


settings = Settings()
