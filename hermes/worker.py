#!/usr/bin/env python3
"""Process the server-filtered queue with the conservative browser runner."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote

from hermes.browser import run_application
from hermes.client import ApiError, Client

ROOT = Path(__file__).resolve().parent.parent


def load_profile(path: str | None = None) -> dict:
    profile_path = Path(path or os.environ.get("PROFILE_PATH", ROOT / "config/profile.toml"))
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    if not profile_path.is_file():
        raise RuntimeError(
            f"missing profile {profile_path}; copy config/profile.example.toml and fill it"
        )
    with profile_path.open("rb") as handle:
        profile = tomllib.load(handle)
    profile["account"] = {
        "email": os.environ.get(
            "APPLICATION_ACCOUNT_EMAIL",
            profile.get("identity", {}).get("email", ""),
        ),
        "password": os.environ.get("APPLICATION_ACCOUNT_PASSWORD", ""),
    }
    return profile


def lease_body(claim: dict) -> dict:
    return {
        "attempt_id": claim["attempt_id"],
        "lease_token": claim["lease_token"],
    }


def process_role(client: Client, role: dict, profile: dict, dry_run: bool) -> str:
    role_path = quote(role["id"], safe="")
    claim = client.post(
        f"/roles/{role_path}/claim",
        {"worker_id": os.environ.get("HOSTNAME", "local-hermes"), "dry_run": dry_run},
    )
    lease = lease_body(claim)
    overrides = client.get(f"/roles/{role_path}/answer-overrides").get("answers", {})
    recorded = False
    submitting = False

    def before_submit(questions: list[dict]) -> None:
        nonlocal recorded, submitting
        client.post(
            f"/roles/{role_path}/questions",
            {**lease, "questions": questions},
        )
        recorded = True
        client.post(
            f"/roles/{role_path}/attempt-state",
            {**lease, "state": "submitting", "detail": "final policy check passed"},
        )
        submitting = True

    try:
        result = run_application(
            claim["role"],
            profile,
            dry_run=dry_run,
            browser_state=ROOT / "browser-state",
            before_submit=before_submit,
            answer_overrides=overrides,
        )
        if not recorded:
            client.post(
                f"/roles/{role_path}/questions",
                {**lease, "questions": result["questions"]},
            )
        if result["state"] == "pending":
            blocked_questions = [
                question for question in result["questions"]
                if question.get("disposition") == "pending" or question.get("blocker")
            ][:25]
            client.post(
                f"/roles/{role_path}/flag",
                {
                    **lease,
                    "reason": result["detail"][:1900],
                    "questions": blocked_questions,
                },
            )
        elif result["state"] == "dry_run":
            client.post(
                f"/roles/{role_path}/attempt-state",
                {**lease, "state": "abandoned", "detail": "dry-run preflight passed"},
            )
        elif result["state"] == "submitted":
            client.post(
                f"/roles/{role_path}/apply",
                {
                    **lease,
                    "resume": claim["resume"],
                    "confirmation_url": result["confirmation_url"],
                },
            )
        else:
            client.post(
                f"/roles/{role_path}/attempt-state",
                {**lease, "state": "unknown", "detail": result["detail"]},
            )
        return result["state"]
    except Exception as exc:
        state = "unknown" if submitting else "failed"
        detail = f"{type(exc).__name__}: {exc}"[:1900]
        try:
            if state == "unknown":
                client.post(
                    f"/roles/{role_path}/attempt-state",
                    {**lease, "state": "unknown", "detail": detail},
                )
            else:
                client.post(
                    f"/roles/{role_path}/attempt-state",
                    {**lease, "state": "failed", "detail": detail},
                )
        except Exception:
            pass
        raise


def run(limit: int = 2, role_ids: set[str] | None = None) -> dict[str, int]:
    client = Client()
    profile = load_profile()
    dry_run = os.environ.get("AUTO_SUBMIT", "false").lower() != "true"
    configured_ids = {
        value.strip()
        for value in os.environ.get("CANARY_ROLE_IDS", "").split(",")
        if value.strip()
    }
    role_ids = role_ids or configured_ids
    queue = client.get("/queue", limit=100 if role_ids else limit)
    roles = queue["roles"]
    if role_ids:
        roles = [role for role in roles if role["id"] in role_ids][:limit]
    counts: dict[str, int] = {}
    for role in roles:
        try:
            state = process_role(client, role, profile, dry_run)
        except ApiError as exc:
            print(exc, file=sys.stderr)
            state = "api_error"
        except Exception as exc:
            print(f"{role['company']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            state = "failed"
        counts[state] = counts.get(state, 0) + 1
    return counts


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else int(
        os.environ.get("APPLICATION_BATCH_LIMIT", "2")
    )
    print(run(limit))
