"""Conservative Playwright form runner for explicitly supported ATS hosts."""

from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from hermes.adapters import adapter_for
from hermes.policy import Decision, audit_answer, classify

CONFIRMATION_TEXT = (
    "application submitted",
    "application has been submitted",
    "thank you for applying",
    "we have received your application",
)


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("application URL must be public HTTPS without credentials")
    if adapter_for(url) is None:
        raise ValueError(f"unsupported ATS host: {parsed.hostname}")
    for info in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("application URL resolved to a non-public address")


def _label(element) -> str:
    return element.evaluate(
        """el => {
          const byFor = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
          const wrapping = el.closest('label');
          const fieldset = el.closest('fieldset');
          const labelledBy = (el.getAttribute('aria-labelledby') || '')
            .split(/\\s+/).map(id => document.getElementById(id)?.innerText || '').join(' ');
          return (el.getAttribute('description') ||
                  el.getAttribute('aria-label') ||
                  labelledBy ||
                  (byFor && byFor.innerText) ||
                  (wrapping && wrapping.innerText) ||
                  (fieldset && fieldset.querySelector('legend')?.innerText) ||
                  el.getAttribute('placeholder') || el.name || '').trim();
        }"""
    )


def _field_type(element) -> str:
    if element.get_attribute("role") == "combobox":
        return "combobox"
    tag = element.evaluate("el => el.tagName.toLowerCase()")
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    return (element.get_attribute("type") or "text").lower()


def _choice_label(element) -> str:
    return element.evaluate(
        """el => {
          const byFor = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
          const wrapping = el.closest('label');
          return ((byFor && byFor.innerText) ||
                  (wrapping && wrapping.innerText) ||
                  el.value || '').trim();
        }"""
    )


def _field_options(element, field_type: str) -> list[str]:
    if field_type == "select":
        return [
            value.strip()
            for value in element.locator("option").all_text_contents()
            if value.strip()
        ]
    if field_type == "radio":
        name = element.get_attribute("name")
        if not name:
            return []
        group = element.page.locator(f'input[type="radio"][name="{name}"]')
        return [
            _label(group.nth(index)).strip()
            for index in range(group.count())
            if _label(group.nth(index)).strip()
        ]
    if field_type == "combobox":
        try:
            element.click()
            options = [
                value.strip()
                for value in element.page.locator('[role="option"]').all_text_contents()
                if value.strip()
            ]
            element.press("Escape")
            return options
        except Exception:
            return []
    if field_type == "checkbox":
        name = element.get_attribute("name")
        if not name:
            return [_label(element)]
        group = element.page.locator(f'input[type="checkbox"][name="{name}"]')
        return [
            _choice_label(group.nth(index)).strip()
            for index in range(group.count())
            if _choice_label(group.nth(index)).strip()
        ]
    return []


def _normalized_question(text: str) -> str:
    return " ".join(text.lower().split())[:2000]


def _fill(element, decision: Decision, resume_path: Path | None) -> None:
    field_type = _field_type(element)
    if decision.category == "resume":
        if not resume_path or not resume_path.is_file():
            raise FileNotFoundError(f"resume is missing: {resume_path}")
        element.set_input_files(str(resume_path))
        return
    value = decision.value
    if value is None:
        return
    if field_type == "select":
        answer = "Yes" if value is True else "No" if value is False else str(value)
        try:
            element.select_option(label=answer)
        except Exception:
            element.select_option(value=answer)
    elif field_type == "combobox":
        answer = "Yes" if value is True else "No" if value is False else str(value)
        element.click()
        element.fill(answer)
        options = element.page.locator('[role="option"]')
        for index in range(options.count()):
            option = options.nth(index)
            text = option.inner_text().strip()
            if answer.lower() == text.lower() or answer.lower() in text.lower():
                option.click()
                return
        element.press("Escape")
        raise RuntimeError(f"no matching combobox option for {answer}")
    elif field_type == "radio":
        name = element.get_attribute("name")
        if not name:
            raise RuntimeError("radio group has no name")
        answer = "yes" if value is True else "no" if value is False else str(value).lower()
        group = element.page.locator(f'input[type="radio"][name="{name}"]')
        for index in range(group.count()):
            option = group.nth(index)
            if answer in _label(option).lower() or answer == (option.get_attribute("value") or "").lower():
                option.check()
                return
        raise RuntimeError(f"no matching radio option for {answer}")
    elif field_type == "checkbox":
        name = element.get_attribute("name")
        answer = str(value).lower()
        group = (
            element.page.locator(f'input[type="checkbox"][name="{name}"]')
            if name else element
        )
        for index in range(group.count()):
            option = group.nth(index)
            label = _choice_label(option).lower()
            if answer == label or answer in label:
                option.check()
                return
        raise RuntimeError("checkbox answer requires human review")
    else:
        element.fill(str(value))


def run_application(
    role: dict,
    profile: dict,
    *,
    dry_run: bool,
    browser_state: Path,
    before_submit=None,
    answer_overrides: dict[str, object] | None = None,
) -> dict:
    """Fill one application and return questions plus a final state."""
    from playwright.sync_api import sync_playwright

    validate_url(role["url"])
    adapter = adapter_for(role["url"])
    assert adapter is not None
    resume_value = profile.get("resumes", {}).get(role["best_track"])
    if resume_value:
        resume_path = Path(resume_value).expanduser()
        if not resume_path.is_absolute():
            resume_path = Path(__file__).resolve().parent.parent / resume_path
        resume_path = resume_path.resolve()
    else:
        resume_path = None
    browser_state.mkdir(parents=True, exist_ok=True, mode=0o700)
    headless = os.environ.get("HERMES_HEADLESS", "false").lower() == "true"
    questions: list[dict] = []
    blockers: list[str] = []
    answer_overrides = answer_overrides or {}

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(browser_state), headless=headless, accept_downloads=False
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(role["url"], wait_until="domcontentloaded", timeout=60_000)
        validate_url(page.url)
        adapter.prepare(page)
        fields = page.locator(
            "input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea"
        )
        seen_choice_groups: set[str] = set()
        for index in range(fields.count()):
            element = fields.nth(index)
            if (
                not element.is_visible()
                or element.is_disabled()
                or element.get_attribute("aria-hidden") == "true"
            ):
                continue
            field_type = _field_type(element)
            if field_type in {"radio", "checkbox"}:
                radio_name = element.get_attribute("name") or f"radio-{index}"
                if radio_name in seen_choice_groups:
                    continue
                seen_choice_groups.add(radio_name)
            label = _label(element)
            required = bool(
                element.get_attribute("required") is not None
                or element.get_attribute("aria-required") == "true"
            )
            decision = classify(label, field_type, required, profile)
            approved = answer_overrides.get(_normalized_question(label))
            if approved is not None and decision.category != "free_text":
                decision = Decision(
                    "approved", "filled", "role_answer_override", approved
                )
            if decision.category == "resume":
                decision = Decision(
                    "resume",
                    "filled" if resume_path and resume_path.is_file()
                    else ("pending" if required else "skipped_optional"),
                    f"resumes.{role['best_track']}",
                    str(resume_path) if resume_path else None,
                )
            redacted, answer_hash = audit_answer(decision)
            question = {
                "text": label or "<unlabelled field>",
                "field_type": field_type,
                "required": required,
                "category": decision.category,
                "disposition": decision.disposition,
                "profile_key": decision.profile_key,
                "answer_redacted": redacted,
                "answer_hash": answer_hash,
                "options": _field_options(element, field_type),
                "proposed_answer": decision.value,
                "evidence": (
                    f"From {decision.profile_key}" if decision.profile_key else None
                ),
                "blocker": None,
            }
            questions.append(question)
            if decision.blocks:
                question["blocker"] = "Human answer required"
                blockers.append(label or "<unlabelled required field>")
                continue
            if decision.disposition == "filled":
                try:
                    _fill(element, decision, resume_path)
                except Exception as exc:
                    question["blocker"] = f"{type(exc).__name__}: could not select or fill"
                    blockers.append(f"{label}: {type(exc).__name__}")

        if blockers:
            context.close()
            unique_blockers = list(dict.fromkeys(blockers))
            detail = "; ".join(unique_blockers)
            if len(detail) > 1900:
                detail = detail[:1850] + f"… (+{len(unique_blockers)} blocked fields)"
            return {"state": "pending", "questions": questions, "detail": detail}
        if dry_run:
            context.close()
            return {"state": "dry_run", "questions": questions, "detail": "preflight passed"}

        if before_submit is None:
            raise RuntimeError("live submission requires a pre-submit policy callback")
        before_submit(questions)
        adapter.submit(page)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        body = page.locator("body").inner_text(timeout=15_000).lower()
        confirmation = next((text for text in CONFIRMATION_TEXT if text in body), None)
        result = {
            "state": "submitted" if confirmation else "unknown",
            "questions": questions,
            "detail": confirmation or "no positive confirmation after final click",
            "confirmation_url": page.url if confirmation else None,
        }
        context.close()
        return result
