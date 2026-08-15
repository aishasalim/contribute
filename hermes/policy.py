"""Deterministic form-field classification; the model never invents answers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

DEMOGRAPHIC = re.compile(
    r"\b(gender|sex|race|ethnic|veteran|disabilit|self.identif|sexual orientation|"
    r"gender identity|pronouns?|hispanic|latino)\b", re.I
)
FREE_TEXT = re.compile(
    r"\b(why (?:do you|this|are you)|describe|tell us|cover letter|"
    r"additional information|anything else|project you|essay|motivation)\b", re.I
)

DEMOGRAPHIC_FIELDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(gender|sex)\b(?!ual orientation)", re.I), "demographics.gender"),
    (re.compile(r"sexual orientation", re.I), "demographics.sexual_orientation"),
    (re.compile(r"\bveteran", re.I), "demographics.veteran_status"),
    (re.compile(r"\bdisabilit", re.I), "demographics.disability_status"),
]

PROFILE_FIELDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"first.?name", re.I), "identity.first_name"),
    (re.compile(r"last.?name|surname", re.I), "identity.last_name"),
    (re.compile(r"\b(password|passcode)\b", re.I), "account.password"),
    (re.compile(r"\bemail\b", re.I), "identity.email"),
    (re.compile(r"\b(phone|mobile)\b", re.I), "identity.phone"),
    (re.compile(r"\b(?:date of birth|birth date|dob)\b", re.I),
     "identity.birth_date"),
    (re.compile(r"\bbirth month\b", re.I), "identity.birth_month"),
    (re.compile(r"\bbirth day\b", re.I), "identity.birth_day"),
    (re.compile(r"\bbirth year\b", re.I), "identity.birth_year"),
    (re.compile(r"(?:at least|over)\s+18|18\s+years?.*older|age of 18", re.I),
     "identity.age_18_or_older"),
    (re.compile(r"\bage\b", re.I), "identity.age"),
    (re.compile(r"\baddress\b", re.I), "identity.address"),
    (re.compile(r"\bcity\b", re.I), "identity.city"),
    (re.compile(r"\b(state|province)\b", re.I), "identity.state"),
    (re.compile(r"zip|postal", re.I), "identity.postal_code"),
    (re.compile(r"authori[sz]ed.*work|work authori[sz]ation", re.I),
     "eligibility.authorized_to_work_us"),
    (re.compile(r"sponsor|sponsorship", re.I), "eligibility.requires_sponsorship"),
    (re.compile(r"\bcountry\b", re.I), "identity.country"),
    (re.compile(r"school|university|college", re.I), "education.school"),
    (re.compile(r"\bdegree\b", re.I), "education.degree"),
    (re.compile(r"major|field of study|discipline", re.I), "education.major"),
    (re.compile(r"\bgpa\b|grade point average", re.I), "education.gpa"),
    (re.compile(r"expected graduation date|graduate.*date", re.I),
     "education.graduation_date"),
    (re.compile(r"graduation.*month", re.I), "education.graduation_month"),
    (re.compile(r"graduation.*year|graduate.*year", re.I), "education.graduation_year"),
    (re.compile(r"linkedin", re.I), "links.linkedin"),
    (re.compile(r"github", re.I), "links.github"),
    (re.compile(r"portfolio|website", re.I), "links.portfolio"),
    (re.compile(
        r"(?:citizen.*permanent resident|permanent resident.*citizen)", re.I
     ), "eligibility.permanent_resident"),
    (re.compile(r"\b(?:u\.?s\.?\s+)?citizen(?:ship)?\b", re.I),
     "eligibility.us_citizen"),
    (re.compile(r"permanent resident|green card", re.I),
     "eligibility.permanent_resident"),
    (re.compile(r"immigration status", re.I), "eligibility.immigration_status"),
    (re.compile(r"willing.*relocat|relocat.*willing", re.I),
     "availability.willing_to_relocate"),
    (re.compile(r"availab.*summer\s+2027|summer\s+2027.*availab", re.I),
     "availability.summer_2027"),
    (re.compile(r"start date month", re.I), "availability.start_month"),
    (re.compile(r"start date day", re.I), "availability.start_day"),
    (re.compile(r"start date year", re.I), "availability.start_year"),
    (re.compile(r"end date month", re.I), "availability.end_month"),
    (re.compile(r"end date day", re.I), "availability.end_day"),
    (re.compile(r"end date year", re.I), "availability.end_year"),
    (re.compile(r"(?:internship|availability|available|work).*(?:end date|until)|"
                r"(?:end date|until).*(?:internship|availability|available|work)",
                re.I), "availability.end_date"),
    (re.compile(r"(?:available|availability|start).*(?:date|when)|"
                r"(?:date|when).*(?:available|start)", re.I),
     "availability.start_date"),
    (re.compile(r"hourly (?:rate|pay)|pay per hour", re.I),
     "compensation.hourly_rate"),
    (re.compile(r"salary expectation|expected salary|desired compensation|"
                r"compensation expectation|desired pay", re.I),
     "compensation.salary_expectation"),
]


@dataclass(frozen=True)
class Decision:
    category: str
    disposition: str
    profile_key: str | None = None
    value: Any = None

    @property
    def blocks(self) -> bool:
        return self.disposition == "pending"


def profile_value(profile: dict, dotted: str) -> Any:
    value: Any = profile
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def classify(label: str, field_type: str, required: bool, profile: dict) -> Decision:
    text = " ".join(label.split())
    if re.search(r"\bcover letter\b", text, re.I):
        return Decision("free_text", "pending" if required else "skipped_optional")
    for pattern, key in DEMOGRAPHIC_FIELDS:
        if pattern.search(text):
            value = profile_value(profile, key)
            if value not in (None, ""):
                return Decision("demographic", "filled", key, value)
            return Decision(
                "demographic", "pending" if required else "skipped_demographic", key
            )
    if DEMOGRAPHIC.search(text):
        return Decision(
            "demographic", "pending" if required else "skipped_demographic"
        )
    if re.search(r"\btranscript\b", text, re.I):
        value = profile_value(profile, "documents.transcript")
        if value not in (None, ""):
            return Decision("document", "filled", "documents.transcript", value)
        return Decision(
            "document", "pending" if required else "skipped_optional",
            "documents.transcript",
        )
    if field_type == "file" or re.search(r"\b(resume|cv)\b", text, re.I):
        return Decision("resume", "filled", "resumes")
    if field_type == "textarea" or FREE_TEXT.search(text):
        return Decision("free_text", "pending" if required else "skipped_optional")
    for pattern, key in PROFILE_FIELDS:
        if pattern.search(text):
            value = profile_value(profile, key)
            if value not in (None, ""):
                if key == "education.major":
                    fallback = profile_value(profile, "education.major_fallback")
                    if fallback not in (None, ""):
                        value = [value, fallback]
                return Decision("profile", "filled", key, value)
            return Decision("profile", "pending" if required else "skipped_optional", key)
    return Decision("unknown", "pending" if required else "skipped_optional")


def audit_answer(decision: Decision) -> tuple[str | None, str | None]:
    if decision.category == "demographic" or decision.value is None:
        return None, None
    raw = str(decision.value)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if isinstance(decision.value, bool):
        redacted = "yes" if decision.value else "no"
    else:
        redacted = f"<from {decision.profile_key}>"
    return redacted, digest
