#!/usr/bin/env python3
"""Read-only Gmail scanner with conservative, auditable role matching."""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from hermes.client import Client

ROOT = Path(__file__).resolve().parent.parent
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
STATUS_CLASSES = {"rejected", "phone_screen", "in_progress", "offer"}

REJECTION = re.compile(
    r"(not (?:be )?moving forward|decided not to proceed|"
    r"unfortunately.{0,80}(?:position|application|moving forward)|"
    r"will not be advancing|other candidates|not selected)", re.I | re.S
)
OFFER = re.compile(
    r"(pleased to (?:extend|present).{0,30}offer|offer of employment|formal offer)", re.I | re.S
)
INTERVIEW = re.compile(
    r"(schedule.{0,30}interview|invitation to interview|interview availability|"
    r"technical interview|phone screen)", re.I | re.S
)
ADVANCE = re.compile(
    r"(next step|moving forward with your application|assessment|coding challenge)", re.I
)
RECEIVED = re.compile(r"(application (?:was )?received|thank you for applying)", re.I)
STOPWORDS = {
    "intern", "internship", "engineer", "engineering", "developer", "software",
    "the", "and", "for", "with", "summer", "fall", "winter", "spring",
}


def classify(text: str) -> str:
    if REJECTION.search(text):
        return "rejected"
    if OFFER.search(text):
        return "offer"
    if INTERVIEW.search(text):
        return "phone_screen"
    if ADVANCE.search(text):
        return "in_progress"
    if RECEIVED.search(text):
        return "received"
    return "other"


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _company_key(company: str) -> str:
    words = [
        word for word in _normal(company).split()
        if word not in {"inc", "llc", "ltd", "corporation", "corp", "company", "co"}
    ]
    return " ".join(words)


def _title_tokens(title: str) -> set[str]:
    return {
        word for word in _normal(title).split()
        if len(word) > 2 and word not in STOPWORDS
    }


def _requisition_tokens(url: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-z0-9_-]{4,}", urlparse(url).path)
        if any(char.isdigit() for char in token)
    }


def score_match(role: dict, sender: str, subject: str, body: str) -> tuple[float, list[str]]:
    haystack = _normal(f"{sender} {subject} {body}")
    company = _company_key(role["company"])
    score, evidence = 0.0, []
    if company and company in haystack:
        score += 0.55
        evidence.append("company")
    domain_words = set(_normal(urlparse("mailto:" + sender).path.split("@")[-1]).split())
    company_words = set(company.split())
    if company_words and domain_words & company_words:
        score += 0.2
        evidence.append("sender-domain")
    title = _title_tokens(role["title"])
    if title:
        overlap = len(title & set(haystack.split())) / len(title)
        if overlap >= 0.5:
            score += min(0.2, overlap * 0.2)
            evidence.append("title")
    requisitions = _requisition_tokens(role.get("url") or "")
    if requisitions and any(token in body.lower() for token in requisitions):
        score += 0.45
        evidence.append("requisition")
    if role.get("applied"):
        score += 0.05
        evidence.append("application-window")
    return min(score, 1.0), evidence


def choose_match(
    roles: list[dict], sender: str, subject: str, body: str
) -> tuple[dict | None, float, list[str], bool]:
    ranked = sorted(
        ((role, *score_match(role, sender, subject, body)) for role in roles),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] < 0.5:
        return None, 0.0, [], False
    best = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    unique = best[1] >= 0.9 and best[1] - second_score >= 0.15
    return best[0], best[1], best[2], unique


def _payload_text(payload: dict) -> str:
    chunks: list[str] = []
    body = payload.get("body", {}).get("data")
    if body:
        chunks.append(base64.urlsafe_b64decode(body + "==").decode(errors="replace"))
    for part in payload.get("parts", []):
        if part.get("mimeType") in {"text/plain", "text/html", "multipart/alternative"}:
            chunks.append(_payload_text(part))
    return re.sub(r"<[^>]+>", " ", "\n".join(chunks))[:100_000]


def _headers(payload: dict) -> dict[str, str]:
    return {
        item["name"].lower(): item["value"]
        for item in payload.get("headers", [])
    }


def gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials_path = Path(os.environ.get("GMAIL_CREDENTIALS", ROOT / "credentials.json"))
    token_path = Path(os.environ.get("GMAIL_TOKEN", ROOT / "token.json"))
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            credentials = flow.run_local_server(port=0)
        token_path.write_text(credentials.to_json())
        token_path.chmod(0o600)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def scan() -> dict[str, int]:
    client = Client()
    roles = []
    for status in ("applied", "in_progress", "phone_screen"):
        roles.extend(client.get(
            "/roles", status=status, limit=500, paid_only="false", include_dead="true"
        )["roles"])
    service = gmail_service()
    listing = service.users().messages().list(
        userId="me", q="newer_than:2d", maxResults=200
    ).execute()
    counts = {"update": 0, "pending": 0, "ignore": 0, "duplicate": 0}
    for item in listing.get("messages", []):
        message = service.users().messages().get(
            userId="me", id=item["id"], format="full"
        ).execute()
        headers = _headers(message["payload"])
        body = _payload_text(message["payload"])
        sender, subject = headers.get("from", ""), headers.get("subject", "")
        kind = classify(f"{subject}\n{body}")
        role, confidence, evidence_parts, unique = choose_match(
            roles, sender, subject, body
        )
        if kind in STATUS_CLASSES and unique:
            decision = "update"
        elif kind in STATUS_CLASSES and role:
            decision = "pending"
        else:
            decision = "ignore"
        received = parsedate_to_datetime(headers.get("date", "")) if headers.get("date") else None
        if not received:
            received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
        evidence = ", ".join(evidence_parts) or "no reliable role evidence"
        response = client.post("/email-observations", {
            "message_id": item["id"],
            "received_at": received.isoformat(),
            "classification": kind,
            "matched_role_id": role["id"] if role else None,
            "confidence": round(confidence, 3),
            "evidence": evidence,
            "decision": decision,
        })
        key = "duplicate" if response.get("duplicate") else decision
        counts[key] += 1
    return counts


if __name__ == "__main__":
    print(json.dumps(scan(), indent=2))
