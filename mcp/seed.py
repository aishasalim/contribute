#!/usr/bin/env python3
"""seed — build data/roles.json for the first time.

Two inputs:
  1. The application spreadsheet, imported once as history (SHEET below).
  2. A live harvest of the registered ATS boards.

Run it once. After that, `find_roles` and `sheet_pull` keep the file current.
Re-running is safe: the merge appends and never overwrites application state.

    python3 mcp/seed.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import radar  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROLES = ROOT / "data" / "roles.json"
CACHE = ROOT / "data" / "descriptions.cache.json"

YEAR = "2026"  # every spreadsheet row falls in this year

RESUMES = [
    {
        "id": "swe",
        "label": "SWE",
        "file": "resumes/aisha-swe.pdf",
        "color": "#2f6fd6",
        "titles": [
            "software engineer", "software engineering", "software developer",
            "backend", "back end", "full stack", "fullstack", "platform engineer",
            "infrastructure engineer", "systems engineer", "web developer",
            "site reliability", "developer intern", "engineering intern",
        ],
        "keywords": [
            "backend", "frontend", "full stack", "api", "microservice",
            "distributed system", "kubernetes", "docker", "aws", "cloud",
            "typescript", "javascript", "react", "golang", "java", "sql",
            "database", "scalability", "rest", "grpc", "ci/cd", "linux",
        ],
    },
    {
        "id": "ml",
        "label": "ML",
        "file": "resumes/aisha-ml.pdf",
        "color": "#7c4ddb",
        "titles": [
            "machine learning", "ml engineer", "ai engineer", "ai platform",
            "research intern", "research scientist", "data science", "data scientist",
            "deep learning", "applied scientist", "mlops", "ai software",
            "ai research", "ai solutions",
        ],
        "keywords": [
            "machine learning", "deep learning", "neural network", "pytorch",
            "tensorflow", "llm", "large language model", "transformer", "nlp",
            "computer vision", "inference", "training", "model", "dataset",
            "reinforcement learning", "mlops", "cuda", "gpu", "embedding",
            "fine-tun", "generative",
        ],
    },
    {
        "id": "hwv",
        "label": "HW Verif",
        "file": "resumes/aisha-hardware-verification.pdf",
        "color": "#12b5a5",
        "titles": [
            "design verification", "verification engineer", "hardware engineer",
            "silicon", "asic", "fpga", "rtl", "digital design", "soc",
            "physical design", "product engineer", "validation engineer",
            "firmware engineer", "embedded software", "hardware validation",
        ],
        "keywords": [
            "systemverilog", "verilog", "vhdl", "uvm", "rtl", "testbench",
            "cocotb", "asic", "fpga", "soc", "silicon", "tapeout", "synthesis",
            "timing", "verification", "validation", "simulation", "waveform",
            "firmware", "embedded", "hardware", "eda", "coverage", "assertion",
            "post-silicon", "bring-up", "jtag", "pcie", "ddr", "axi",
        ],
    },
]

# ------------------------------------------------------------------ spreadsheet
# (company, title, date_found MM/DD, date_applied MM/DD, status, notes)
SHEET = [
    ("American Outdoor Brands", "", "06/30", "06/30", "applied", ""),
    ("American Heart Association", "Intern, Infrastructure Operations & Automation - Dallas, TX", "07/03", "07/03", "applied", ""),
    ("American Heart Association", "Intern, BT Cybersecurity Operations - Dallas, TX", "07/03", "07/03", "applied", ""),
    ("Cotton Holdings", "AI Solutions Fellowship", "07/07", "07/07", "applied", ""),
    ("DTCC", "Information Technology Intern [2027 Summer Program]", "07/09", "07/09", "applied", ""),
    ("Capital One", "", "", "", "applied", "Exact date not in inbox; applied before 07/06 (offer-prep email received then)"),
    ("Rippling", "Full Stack Software Engineer Intern - Winter 2027", "07/13", "07/13", "applied", ""),
    ("Velinsa", "Software Engineering Intern", "07/13", "07/13", "applied", ""),
    ("Cohere", "Software Engineer Intern (Fall/Winter 2026)", "07/13", "07/13", "applied", ""),
    ("Citadel", "Software Engineer - Intern (US)", "07/13", "07/13", "rejected", ""),
    ("Houchens Insurance Group", "", "07/13", "07/13", "applied", ""),
    ("Rackner", "", "07/13", "07/13", "applied", ""),
    ("iHerb", "", "07/13", "07/13", "applied", ""),
    ("DRW", "", "07/13", "07/13", "rejected", "Rejected for FPGA Intern role specifically (update received 07/22)"),
    ("M&T Bank", "2027 Technology Internship Program", "07/13", "07/13", "applied", ""),
    ("M&T Bank", "2027 Technology Development Program", "07/13", "07/13", "applied", ""),
    ("Amazon", "Software Development Engineer Internship - Fall 2026 (US)", "", "07/13", "applied", "07/16 email flagged this application as incomplete - may need to resubmit/verify"),
    ("Danaher", "Software Automation Internships", "", "07/14", "applied", ""),
    ("Danaher", "Software Engineer Internships", "", "07/14", "applied", ""),
    ("Akuna Capital", "Software Engineer Intern - Full Stack Web, Summer 2027", "", "07/14", "applied", ""),
    ("Formlabs", "", "", "07/16", "applied", ""),
    ("Netic", "AI Software Intern (Fall 2026)", "", "07/16", "rejected", ""),
    ("Caterpillar", "2027 IT Intern (Evergreen)", "", "07/16", "applied", ""),
    ("Caterpillar", "2027 Internship - Solutions Platforms Engineered (Evergreen)", "", "07/16", "applied", ""),
    ("DNV", "AI Research Internship - 7094", "", "07/16", "applied", ""),
    ("Optiver", "Trading Automation and Operations Intern (Summer 2027)", "", "07/16", "applied", "Assessment invitations received 07/22, 07/25, 07/28"),
    ("Google", "", "", "", "applied", "Response received 07/20; exact apply date not in inbox"),
    ("Western Digital", "Summer 2027 - Software Engineering Internship", "", "07/21", "applied", ""),
    ("American Heart Association", "Intern, Data Science - Remote", "", "", "rejected", "Response received 07/22; original apply date not in inbox"),
    ("Comtech Telecommunications", "Software Engineering Co-Op", "", "07/23", "applied", ""),
    ("Rendezvous Robotics", "", "", "07/23", "rejected", ""),
    ("Nokia", "Internship - Software Development - 38408", "", "07/23", "applied", ""),
    ("Nokia", "AI R&D Engineer Co-op - 38783", "", "07/23", "rejected", ""),
    ("Texas Instruments", "Career Accelerator Program - Product Engineer - 25003917", "", "07/23", "applied", ""),
    ("Texas Instruments", "Career Accelerator Program - Product Engineer - 25016672", "", "07/23", "applied", ""),
    ("Walleye Capital", "Quantitative Developer Intern (Summer 2027)", "", "07/23", "rejected", ""),
    ("Core & Main", "Intern - Corporate", "", "07/25", "rejected", ""),
    ("Simtra BioPharma Solutions", "", "", "07/25", "rejected", ""),
    ("MSM", "AI Solutions Co-op (Fall 2026)", "", "07/25", "applied", ""),
    ("Rockefeller Capital Management", "Summer Analyst - Core Platforms", "", "07/25", "applied", ""),
    ("BlackEdge Capital", "", "", "07/25", "rejected", ""),
    ("Metrea", "Engineering Co-op", "", "07/25", "applied", ""),
    ("Robert Bosch LLC", "Autonomous Driving - Internship in Machine Learning", "", "07/25", "applied", ""),
    ("PDT Partners", "Summer 2027 Systems Engineering Intern", "", "07/25", "rejected", ""),
    ("Baxter Aerospace Inc.", "Embedded Software Engineering Intern", "", "07/25", "applied", ""),
    ("LUZCO Technologies", "AI Solutions Co-Op", "", "07/25", "rejected", ""),
    ("Nextiva", "Forward Deployed Engineer Intern - AI Implementation", "", "07/25", "rejected", ""),
    ("Intel", "AI Software Engineering Intern (Job JR0282639)", "", "07/30", "applied", ""),
    ("Microsoft", "Software Engineer: Intern Opportunities for University Students - CoreAI (Job 200046156)", "", "08/03", "applied", ""),
    ("Boeing", "Summer 2027 Internship Program (Paid) - Program Management", "", "08/03", "in_progress", "Rejected 08/04 due to requisition error, then reactivated 08/10 - back in consideration"),
    ("JPMorgan Chase", "2027 Data & AI Program - Summer Internship - Analyst - US (Job 210773869)", "", "08/03", "applied", ""),
    ("American Express", "Campus Undergraduate Summer Internship Program - 2027 Digital Product, Amex Digital Labs - NY (26011918)", "", "08/03", "applied", ""),
    ("Chicago Trading Company (CTC)", "Software Engineering Internship - Summer 2027", "", "08/04", "applied", ""),
    ("Studyfetch", "Engineering Intern", "", "08/04", "applied", ""),
    ("ESG Global", "Firmware Engineer Intern", "", "08/04", "rejected", ""),
    ("GE Vernova", "Digital Technology Internship - Summer 2027", "", "08/04", "applied", ""),
    ("Praxis Engineering (GDIT)", "Summer 2027 Software Developer Internship", "", "08/04", "applied", ""),
    ("Cadence", "Software Engineering Internship - Summer 2027", "", "08/04", "applied", ""),
    ("Microsoft", "Research Intern - Firmware Security (Job 200046631)", "", "08/04", "applied", "Action needed: reference letter requested to support application"),
    ("ByteDance", "Software Engineer Intern (AI Platform) - 2027 Summer", "", "08/04", "applied", ""),
    ("SPREEAI", "Mobile Software Engineer Intern - Flagship Apps (iOS/Android/Web)", "", "08/05", "applied", ""),
    ("Parsons", "Bridge Engineering Intern - Summer 2027", "", "08/05", "applied", ""),
    ("HNTB", "Returning Intern Engineer - Summer 2027 - Mid Atlantic Division", "", "08/05", "applied", ""),
    ("Roblox", "[Summer 2027] Software Engineer Intern", "", "08/06", "applied", "Email verification reminder received 08/09 - may need to verify to proceed"),
    ("Roblox", "[2027] Software Engineer, Early Career", "", "08/06", "applied", ""),
    ("Orlando Health", "Dig Health Innov Coord Intern 2026-317390", "", "08/06", "applied", ""),
    ("ServiceNow", "Software Engineer, Agentic AI Harness & Quality - Moveworks", "", "08/06", "applied", ""),
    ("HUGO BOSS", "Fall Internship 2026 - IT Intern (AI)", "", "08/06", "applied", ""),
    ("Figure", "", "", "08/06", "applied", ""),
    ("Terranova", "Software Engineering Intern", "", "08/07", "applied", ""),
    ("Tulip Interfaces", "", "", "08/07", "applied", ""),
    ("ING", "Summer 2027 Internship - Tech (Innovation)", "", "08/10", "applied", ""),
    ("The Nuclear Company", "Summer 2027 Software Engineering Intern", "", "08/10", "applied", ""),
    ("The Nuclear Company", "Platform & AI Pre-Engineer", "", "08/12", "applied", ""),
]


def _iso(mmdd: str) -> str | None:
    """MM/DD -> YYYY-MM-DD. The sheet stores no year; every row is 2026."""
    if not mmdd or "/" not in mmdd:
        return None
    month, day = mmdd.split("/")
    return f"{YEAR}-{month.zfill(2)}-{day.zfill(2)}"


def from_sheet() -> list[dict]:
    roles = []
    for row_no, (company, title, found, applied, status, notes) in enumerate(SHEET, start=3):
        applied_iso = _iso(applied)
        note = notes
        if not title:
            # documented hazard: a company with no role title cannot be matched
            note = (note + " " if note else "") + "needs_human: no role title in the sheet"
        role = radar._blank_role(
            id=radar.role_id(company, title or f"row-{row_no}"),
            company=company,
            title=title or f"{company} — role not recorded",
            source="sheet",
            found=_iso(found) or applied_iso or f"{YEAR}-07-13",
            posted=None,
            description="",
            tags=radar.tags_of(title),
            season=radar.season_of(title),
            eligibility={"sponsorship": None, "citizenship": None, "class_year": []},
        )
        role["application"] = {
            "status": status, "applied": applied_iso, "resume": None,
            "sheet_row": row_no, "recruiter": "", "network": "",
            "thank_you": False, "follow_up": False, "notes": note,
        }
        roles.append(role)
    return roles


def main() -> None:
    roles = from_sheet()
    print(f"spreadsheet: imported {len(roles)} rows")

    scope = "all" if "--all" in sys.argv else "priority"
    harvested, stats = radar.harvest(
        scope=scope, progress=lambda n, t: print(f"  ... {n}/{t} boards", flush=True))
    print(f"harvest ({scope}): {stats['ok']}/{stats['boards']} boards ok, "
          f"{stats['failed']} failed, {stats['seen']} postings seen, "
          f"{stats['kept']} early-career US roles kept")
    for name, n in stats["top"]:
        print(f"  {name}: {n}")
    if stats["errors"]:
        print(f"  errors: {stats['errors']}")
    added, updated = radar.merge(roles, harvested)
    print(f"merge: +{added} new, {updated} already known")

    for role in roles:
        radar.score_role(role, RESUMES)

    cache = radar.split_descriptions(roles)
    CACHE.write_text(json.dumps(cache) + "\n")

    data = {
        "meta": {
            "owner": "aishasalim",
            "title": "Internship radar",
            "seasons": ["summer-2027", "fall-2026", "winter-2027", "spring-2027"],
            "generated": radar.today() + "T00:00:00Z",
            "updated_by": "manual",
            "schema": 1,
            "sheet": "https://docs.google.com/spreadsheets/d/1afc67q-MdqMuV5lhJqVRs1X0EbclHwM9iTT29g5-hro/edit",
            "note": "Page 2 of contributie. Roles scored against three resumes. "
                    "The spreadsheet stays the source of truth for application state; "
                    "the radar is the source of truth for discovery and relevancy.",
        },
        "resumes": RESUMES,
        "roles": roles,
    }
    ROLES.write_text(json.dumps(data, indent=2) + "\n")

    tiers: dict[str, int] = {}
    for role in roles:
        tiers[role["tier"]] = tiers.get(role["tier"], 0) + 1
    print(f"wrote {ROLES.relative_to(ROOT)}: {len(roles)} roles, tiers={tiers}")
    print(f"wrote {CACHE.relative_to(ROOT)}: {len(cache)} descriptions cached")


if __name__ == "__main__":
    main()
