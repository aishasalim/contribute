#!/usr/bin/env python3
"""Guards for the two filters that decide what reaches the board.

Run: python3 tests/test_filters.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))
import radar

# (title, should_be_kept)
TITLES = [
    # off-track disciplines must never reach the board
    ("Pharmacy Intern", False),
    ("Radiology Tech Intern / Casual | SVH", False),
    ("Intern - Finance - Florence, Italy", False),
    ("Returning Intern Inspector - Summer 2027", False),
    ("Marketing Intern", False),
    ("Civil Engineering Intern", False),
    ("Mechanical Engineering Intern", False),
    ("Business Development & Sponsorships Intern", False),
    ("Nursing Intern", False),
    ("Process Engineering Co-op", False),
    ("Bridge Engineering Intern", False),
    ("Graphic Designer Intern", False),
    ("Customer Success Intern", False),
    # on-track roles must survive
    ("Software Engineering Intern", True),
    ("Database Engineering Intern", True),
    ("DevOps Engineering Intern", True),
    ("QA Engineering Intern", True),
    ("Design Verification Engineer, Intern", True),
    ("Machine Learning Intern", True),
    ("Firmware Engineer Intern", True),
    ("Campus ASIC Engineer (Intern)", True),
    ("Data Science Intern", True),
    ("Quantitative Developer Intern", True),
    # an on-track signal rescues an off-track word
    ("Manufacturing Software Engineer Intern", True),
    # not an internship
    ("Senior Software Engineer", False),
    ("Software Engineer, New Grad", False),
    ("International Program Manager", False),
]

PAY = [
    ("This is an unpaid volunteer internship", False),
    ("Interns receive academic credit only", False),
    ("Pay range: $45.00 - $60.00 per hour", True),
    ("Compensation: $30/hr for interns", True),
    ("We offer a competitive salary", True),
    ("Nothing stated here at all", None),
]


def main() -> int:
    bad = 0
    for title, want in TITLES:
        got = radar.is_early_career(title)
        if got != want:
            bad += 1
            print(f"FAIL  is_early_career({title!r}) = {got}, want {want}")
    for text, want in PAY:
        got, _ = radar.pay_of(text)
        if got != want:
            bad += 1
            print(f"FAIL  pay_of({text!r}) = {got}, want {want}")
    total = len(TITLES) + len(PAY)
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
