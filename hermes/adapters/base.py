"""ATS-specific navigation with a shared conservative form engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Adapter:
    hosts: tuple[str, ...]
    open_form_selectors: tuple[str, ...]
    submit_selectors: tuple[str, ...]

    def matches(self, url: str) -> bool:
        return any(host in url.lower() for host in self.hosts)

    def prepare(self, page) -> None:
        if page.locator("input, select, textarea").count():
            return
        for selector in self.open_form_selectors:
            button = page.locator(selector).first
            if button.count() and button.is_visible():
                button.click()
                page.wait_for_load_state("domcontentloaded")
                return

    def submit(self, page) -> None:
        for selector in self.submit_selectors:
            button = page.locator(selector).first
            if button.count() and button.is_visible() and button.is_enabled():
                button.click()
                return
        raise RuntimeError("no supported submit button found")
