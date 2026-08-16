#!/usr/bin/env python3
"""Read-only inbox scanner with conservative, auditable role matching.

Reads over IMAP with a Google App Password. The mailbox is opened readonly,
so this can classify a reply but never mark, move or delete one.
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from hermes.client import Client

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


def _message_text(message) -> str:
    """Flatten an email.message.Message to searchable text."""
    chunks: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            chunks.append(payload.decode(charset, errors="replace"))
        except LookupError:
            chunks.append(payload.decode("utf-8", errors="replace"))
    return re.sub(r"<[^>]+>", " ", "\n".join(chunks))[:100_000]


def _decode_header(value: str) -> str:
    """RFC 2047 headers arrive encoded; matching needs the readable text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def gmail_connection() -> imaplib.IMAP4_SSL:
    """Read-only IMAP session using a Google App Password.

    An app password is scoped to mail and revocable on its own, and needs no
    consent browser, so the nightly job runs headless without a token to
    refresh. The mailbox is opened readonly so nothing here can mark, move or
    delete a message.
    """
    address = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not address or not password:
        raise RuntimeError(
            "set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env "
            "(Google account -> Security -> App passwords)"
        )
    connection = imaplib.IMAP4_SSL(
        os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com"),
        int(os.environ.get("GMAIL_IMAP_PORT", "993")),
    )
    connection.login(address, password)
    connection.select("INBOX", readonly=True)
    return connection


def _recent_messages(connection, days: int = 2, limit: int = 200) -> list[dict]:
    """Messages received in the last `days`, newest first.

    IMAP SINCE is date-granular, so this over-selects slightly rather than
    risk missing a reply that landed either side of midnight.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    status, data = connection.search(None, f'(SINCE "{since}")')
    if status != "OK":
        raise RuntimeError(f"IMAP search failed: {status}")
    ids = data[0].split()[-limit:]
    messages = []
    for message_id in reversed(ids):
        status, payload = connection.fetch(message_id, "(RFC822)")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        parsed = email.message_from_bytes(payload[0][1])
        # Message-ID is stable across folders and refetches; the IMAP sequence
        # number is not, and this is what dedupes the observations table.
        identifier = (parsed.get("Message-ID") or "").strip("<> ")
        if not identifier:
            continue
        received = None
        if parsed.get("Date"):
            try:
                received = parsedate_to_datetime(parsed["Date"])
            except (TypeError, ValueError):
                received = None
        messages.append({
            "id": identifier,
            "sender": _decode_header(parsed.get("From", "")),
            "subject": _decode_header(parsed.get("Subject", "")),
            "body": _message_text(parsed),
            "received": received or datetime.now(timezone.utc),
        })
    return messages


def scan() -> dict[str, int]:
    client = Client()
    roles = []
    for status in ("applied", "in_progress", "phone_screen"):
        roles.extend(client.get(
            "/roles", status=status, limit=500, paid_only="false", include_dead="true"
        )["roles"])
    connection = gmail_connection()
    try:
        inbox = _recent_messages(connection)
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    counts = {"update": 0, "pending": 0, "ignore": 0, "duplicate": 0}
    for item in inbox:
        sender, subject, body = item["sender"], item["subject"], item["body"]
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
        evidence = ", ".join(evidence_parts) or "no reliable role evidence"
        response = client.post("/email-observations", {
            "message_id": item["id"],
            "received_at": item["received"].isoformat(),
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
