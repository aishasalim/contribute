#!/usr/bin/env python3
"""radar — role sourcing and relevancy scoring for contributie page 2.

Pure logic, no MCP surface: server.py imports this and wraps it in tools.
Keeping it separate means the scorer can be run from a seed script or from
Hermes without starting an MCP server.

Sourcing reads public ATS job-board endpoints over stdlib urllib, so there is
no extra dependency and no browser. See docs/RADAR.md for the contract.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

UA = "contributie-radar/1 (+https://github.com/aishasalim/contributie)"

# --------------------------------------------------------------- board registry
# 2,272 company -> ATS board rows, vendored from an MIT-licensed upstream list.
# See NOTICE for attribution. `priority` is the hand-verified subset that a
# default harvest polls; the full sweep is opt-in because it takes minutes.
BOARDS_FILE = Path(__file__).resolve().parent.parent / "data" / "boards.json"
BLOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "blocklist.json"


def load_blocklist() -> tuple[set, re.Pattern | None]:
    """Companies and title patterns to drop at harvest time. Unpaid "volunteer
    intern" listings otherwise reach the fit tier and crowd out real roles."""
    if not BLOCK_FILE.exists():
        return set(), None
    b = json.loads(BLOCK_FILE.read_text())
    pats = b.get("title_patterns") or []
    rx = re.compile("|".join(pats), re.I) if pats else None
    return set(b.get("companies") or []), rx


def load_boards() -> dict:
    return json.loads(BOARDS_FILE.read_text())


def board_list(scope: str = "priority", ats: str | None = None) -> list[dict]:
    """scope: priority | all. Optionally narrow to one ATS platform."""
    reg = load_boards()
    rows = reg["companies"]
    if scope == "priority":
        wanted = set(reg["priority"])
        rows = [c for c in rows if c["slug"] in wanted]
    if ats:
        rows = [c for c in rows if c["ats"] == ats]
    return rows

# ------------------------------------------------------------- scoring constants
WEIGHTS = {
    "title": 0.45,
    "keywords": 0.25,
    "seniority": 0.15,
    "eligibility": 0.10,
    "freshness": 0.05,
}
TIERS = ((75, "strong"), (55, "fit"), (35, "stretch"))
KEYWORD_CAP = 6  # distinct keywords needed for a full keyword_density score

EARLY_WORDS = (
    "intern", "internship", "co-op", "coop", "new grad", "new graduate",
    "university", "campus", "student", "summer analyst", "early career",
    "fellowship", "apprentice", "entry level", "early-career",
)
# Internships and co-ops only. Deliberately excludes new grad, early career,
# campus, trainee and apprentice titles: those are full-time roles.
# "Summer analyst" and "fellowship" stay — at banks and labs those ARE the
# internship. Matched against the TITLE only: a description match is worthless,
# because most full-time postings mention "students" in the boilerplate, which
# let ~1,700 senior roles through.
EARLY_TITLE_RE = re.compile(
    r"\bintern(ship)?s?\b|\bco[\s\-]?op(s)?\b|\bsummer\s+analyst\b|\bfellowship\b"
)
# "internal", "international", "alternative" must not read as "intern"
NOT_INTERN_RE = re.compile(r"\bintern(al|ational)")
SENIOR_WORDS = (
    "senior ", "staff ", "principal ", "lead ", "director", "head of",
    "manager,", "vp ", "architect",
)
YEARS_RE = re.compile(r"\b([4-9]|1\d)\+?\s*(?:\+\s*)?years?\b")

SEASON_RES = (
    ("summer-2027", re.compile(r"summer[\s\-–]*2027|2027[\s\-–]*summer")),
    ("fall-2026", re.compile(r"fall[\s\-–]*2026|autumn[\s\-–]*2026|2026[\s\-–]*fall")),
    ("winter-2027", re.compile(r"winter[\s\-–]*2027|2027[\s\-–]*winter")),
    ("spring-2027", re.compile(r"spring[\s\-–]*2027|2027[\s\-–]*spring")),
)

TAG_RES = {
    "Python": r"\bpython\b", "C++": r"c\+\+", "C": r"\bc\b(?!\+)", "Rust": r"\brust\b",
    "Go": r"\bgolang\b|\bgo\b(?= programming| lang)", "Java": r"\bjava\b(?!script)",
    "TypeScript": r"\btypescript\b", "JavaScript": r"\bjavascript\b",
    "React": r"\breact\b", "SQL": r"\bsql\b", "Kubernetes": r"\bkubernetes\b|\bk8s\b",
    "AWS": r"\baws\b", "PyTorch": r"\bpytorch\b", "TensorFlow": r"\btensorflow\b",
    "LLM": r"\bllm\b|large language model", "CUDA": r"\bcuda\b",
    "SystemVerilog": r"systemverilog|system verilog", "UVM": r"\buvm\b",
    "RTL": r"\brtl\b", "Verilog": r"\bverilog\b", "VHDL": r"\bvhdl\b",
    "FPGA": r"\bfpga\b", "ASIC": r"\basic\b", "cocotb": r"\bcocotb\b",
    "Embedded": r"\bembedded\b", "Firmware": r"\bfirmware\b", "Linux": r"\blinux\b",
    "Distributed systems": r"distributed system", "Compilers": r"\bcompiler",
}

SPONSOR_NO = re.compile(
    r"(unable|not able|do not|does not|cannot|will not|won't)[^.]{0,40}sponsor"
    r"|no\s+(visa\s+)?sponsorship|without\s+(visa\s+)?sponsorship"
    r"|sponsorship\s+is\s+not"
)
SPONSOR_YES = re.compile(r"(we|will|do)\s+(offer|provide|consider)[^.]{0,30}sponsor|visa sponsorship available")
CITIZEN_RE = re.compile(
    r"(u\.?s\.?\s+citizen(ship)?\s+(is\s+)?(required|only)"
    r"|must be a u\.?s\.? citizen"
    r"|security clearance|top secret|itar|export control)"
)

NON_US = (
    "canada", "united kingdom", "london", "ireland", "dublin", "germany", "berlin",
    "munich", "france", "paris", "netherlands", "amsterdam", "spain", "madrid",
    "poland", "warsaw", "india", "bangalore", "bengaluru", "hyderabad", "japan",
    "tokyo", "singapore", "australia", "sydney", "melbourne", "china", "beijing",
    "shanghai", "korea", "seoul", "israel", "tel aviv", "brazil", "sao paulo",
    "mexico", "switzerland", "zurich", "sweden", "stockholm", "italy", "portugal",
    "lisbon", "romania", "bucharest", "taiwan", "taipei", "hong kong", "dubai",
    "belgium", "denmark", "norway", "finland", "austria", "czech", "prague",
)


# --------------------------------------------------------------------- utilities
def _get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _plain(raw: str | None) -> str:
    """HTML (or entity-encoded HTML) to flat text."""
    if not raw:
        return ""
    txt = html.unescape(raw)
    if "<" in txt:
        txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    return re.sub(r"\s+", " ", txt).strip()


def _date(value) -> str | None:
    """Anything an ATS calls a timestamp -> YYYY-MM-DD."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):  # Lever uses epoch milliseconds
        return datetime.utcfromtimestamp(value / 1000).strftime("%Y-%m-%d")
    return str(value)[:10]


def role_id(company: str, title: str) -> str:
    """Stable slug. Survives a URL change, so the spreadsheet can point at it."""
    s = re.sub(r"[^a-z0-9]+", "-", f"{company} {title}".lower()).strip("-")
    return s[:90] or "unknown"


def today() -> str:
    return date.today().isoformat()


# -------------------------------------------------------------------- extraction
def season_of(text: str) -> str | None:
    low = text.lower()
    for name, rx in SEASON_RES:
        if rx.search(low):
            return name
    return None


def tags_of(text: str) -> list[str]:
    low = text.lower()
    return [name for name, rx in TAG_RES.items() if re.search(rx, low)]


def eligibility_of(text: str) -> dict:
    low = text.lower()
    sponsorship = None
    if SPONSOR_NO.search(low):
        sponsorship = False
    elif SPONSOR_YES.search(low):
        sponsorship = True
    m = CITIZEN_RE.search(low)
    years = sorted({y for y in re.findall(r"\b(202[5-9]|203[0-1])\b", low)})
    return {
        "sponsorship": sponsorship,
        "citizenship": m.group(0)[:60] if m else None,
        "class_year": years[:4],
    }


def is_early_career(title: str, text: str = "") -> bool:
    """Title-only test. See EARLY_TITLE_RE for why the description is ignored."""
    low = NOT_INTERN_RE.sub(" ", (title or "").lower())
    if not EARLY_TITLE_RE.search(low):
        return False
    return not any(w in low for w in SENIOR_WORDS)


def is_us(location: str) -> bool:
    low = (location or "").lower()
    if not low:
        return True  # unknown location: keep it, let a human judge
    return not any(c in low for c in NON_US)


# ----------------------------------------------------------------------- scoring
def _title_match(title_low: str, resume: dict) -> float:
    if any(t in title_low for t in resume.get("titles", [])):
        return 1.0
    hits = sum(1 for k in resume.get("keywords", []) if k in title_low)
    if hits >= 2:
        return 0.7
    if hits == 1:
        return 0.45
    return 0.0


def _keyword_density(text_low: str, resume: dict) -> tuple[float, list[str]]:
    hits = sorted({k for k in resume.get("keywords", []) if k in text_low})
    return min(len(hits), KEYWORD_CAP) / KEYWORD_CAP, hits


def _seniority(title_low: str, text_low: str) -> float:
    if any(w in title_low for w in EARLY_WORDS):
        return 1.0
    if any(w in text_low[:1500] for w in EARLY_WORDS):
        return 0.8
    if any(w in title_low for w in SENIOR_WORDS):
        return 0.0
    if YEARS_RE.search(text_low):
        return 0.1
    return 0.4


def _eligibility(elig: dict) -> float:
    if elig.get("citizenship"):
        return 0.0
    sponsorship = elig.get("sponsorship")
    if sponsorship is True:
        return 1.0
    if sponsorship is False:
        return 0.35
    return 0.7  # the posting is silent


def _freshness(posted: str | None, found: str | None) -> float:
    stamp = posted or found
    if not stamp:
        return 0.5
    try:
        age = (date.today() - date.fromisoformat(stamp[:10])).days
    except ValueError:
        return 0.5
    if age <= 3:
        return 1.0
    if age >= 30:
        return 0.0
    return round(1 - (age - 3) / 27, 3)


def tier_of(score: int) -> str:
    for floor, name in TIERS:
        if score >= floor:
            return name
    return "none"


def _why(best: dict, resume: dict) -> str:
    """One sentence naming the two signals that carried the score."""
    parts = {
        "title": f'the title reads as {resume["label"]}',
        "keywords": ("the description names " + ", ".join(best["hits"][:3])) if best["hits"] else "",
        "seniority": "it is an internship or co-op posting",
        "eligibility": "no sponsorship or citizenship bar is listed",
        "freshness": "it went up in the last few days",
    }
    ranked = sorted(best["signals"].items(), key=lambda kv: -kv[1] * WEIGHTS[kv[0]])
    picked = [parts[k] for k, v in ranked if v > 0.34 and parts[k]][:2]
    if not picked:
        return "No strong signal for any of the three resumes."
    sentence = "; ".join(picked)
    return sentence[0].upper() + sentence[1:] + "."


def score_role(role: dict, resumes: list[dict]) -> dict:
    """Score one role against every resume track. Mutates and returns the role."""
    title_low = (role.get("title") or "").lower()
    text_low = f"{title_low} {(role.get('description') or '').lower()}"
    elig = role.get("eligibility") or {}

    tracks, detail = {}, {}
    for resume in resumes:
        density, hits = _keyword_density(text_low, resume)
        signals = {
            "title": _title_match(title_low, resume),
            "keywords": density,
            "seniority": _seniority(title_low, text_low),
            "eligibility": _eligibility(elig),
            "freshness": _freshness(role.get("posted"), role.get("found")),
        }
        total = sum(signals[k] * WEIGHTS[k] for k in WEIGHTS)
        tracks[resume["id"]] = int(round(total * 100))
        detail[resume["id"]] = {"signals": signals, "hits": hits}

    best = max(tracks, key=lambda k: tracks[k])
    role["tracks"] = tracks
    role["best_track"] = best
    role["tier"] = tier_of(tracks[best])
    # a second badge when another track lands within 10 points
    role["also_tracks"] = [k for k, v in tracks.items()
                           if k != best and tracks[best] - v <= 10 and v >= 35]
    if role.get("why_by", "auto") == "auto":
        resume = next(r for r in resumes if r["id"] == best)
        role["why"] = _why(detail[best], resume)
        role["why_by"] = "auto"
    return role


# ---------------------------------------------------------------------- fetchers
def _blank_role(**kw) -> dict:
    role = {
        "id": "", "company": "", "title": "", "location": "", "workmode": "unspecified",
        "season": None, "url": "", "source": "manual", "posted": None, "found": today(),
        "description": "", "eligibility": {"sponsorship": None, "citizenship": None, "class_year": []},
        "tags": [], "tracks": {}, "best_track": None, "also_tracks": [], "tier": "none",
        "why": "", "why_by": "auto", "dead": False,
        "application": {
            "status": "none", "applied": None, "resume": None, "sheet_row": None,
            "recruiter": "", "network": "", "thank_you": False, "follow_up": False,
            "notes": "",
        },
    }
    role.update(kw)
    return role


def _workmode(text: str, remote_flag=None) -> str:
    low = text.lower()
    if remote_flag is True or "fully remote" in low or "remote" in low[:60]:
        return "remote"
    if "hybrid" in low:
        return "hybrid"
    if "on-site" in low or "onsite" in low or "in office" in low:
        return "onsite"
    return "unspecified"


def fetch_greenhouse(board: str, display: str = "") -> list[dict]:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        title = j.get("title") or ""
        desc = _plain(j.get("content"))
        loc = (j.get("location") or {}).get("name") or ""
        company = display or j.get("company_name") or board
        out.append(_blank_role(
            id=role_id(company, title), company=company, title=title, location=loc,
            workmode=_workmode(f"{loc} {desc[:400]}"),
            season=season_of(f"{title} {desc[:1500]}"),
            url=j.get("absolute_url") or "", source="greenhouse",
            posted=_date(j.get("first_published") or j.get("updated_at")),
            description=desc, eligibility=eligibility_of(desc),
            tags=tags_of(f"{title} {desc}"),
        ))
    return out


def fetch_ashby(board: str, display: str = "") -> list[dict]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        title = j.get("title") or ""
        desc = _plain(j.get("descriptionPlain") or j.get("descriptionHtml"))
        loc = j.get("location") or ""
        company = display or board.replace("-", " ").title()
        out.append(_blank_role(
            id=role_id(company, title), company=company, title=title, location=loc,
            workmode=_workmode(f"{j.get('workplaceType') or ''} {loc}", j.get("isRemote")),
            season=season_of(f"{title} {desc[:1500]}"),
            url=j.get("jobUrl") or j.get("applyUrl") or "", source="ashby",
            posted=_date(j.get("publishedAt")), description=desc,
            eligibility=eligibility_of(desc), tags=tags_of(f"{title} {desc}"),
        ))
    return out


def fetch_smartrecruiters(board: str, display: str = "") -> list[dict]:
    data = _get_json(f"https://api.smartrecruiters.com/v1/companies/{board}/postings?limit=100")
    out = []
    for j in data.get("content", []):
        title = j.get("name") or ""
        loc = ", ".join(filter(None, [(j.get("location") or {}).get("city"),
                                      (j.get("location") or {}).get("country")]))
        out.append(_blank_role(
            id=role_id(display or board, title), company=display or board,
            title=title, location=loc,
            season=season_of(title), source="smartrecruiters",
            url=f"https://jobs.smartrecruiters.com/{board}/{j.get('id')}",
            posted=_date(j.get("releasedDate")), tags=tags_of(title),
        ))
    return out


def fetch_lever(board: str, display: str = "") -> list[dict]:
    data = _get_json(f"https://api.lever.co/v0/postings/{board}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        title = j.get("text") or ""
        desc = _plain(j.get("descriptionPlain") or j.get("description"))
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        company = display or board.replace("-", " ").title()
        out.append(_blank_role(
            id=role_id(company, title), company=company, title=title, location=loc,
            workmode=_workmode(f"{cats.get('commitment') or ''} {loc}"),
            season=season_of(f"{title} {desc[:1500]}"),
            url=j.get("hostedUrl") or j.get("applyUrl") or "", source="lever",
            posted=_date(j.get("createdAt")), description=desc,
            eligibility=eligibility_of(desc), tags=tags_of(f"{title} {desc}"),
        ))
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
}


def _poll_one(company: dict) -> tuple[dict, list[dict] | None, str]:
    """Poll one board. Never raises: a dead board must not stop the sweep."""
    try:
        return company, FETCHERS[company["ats"]](company["slug"], company.get("name", "")), ""
    except Exception as e:  # network, JSON, or a board that no longer exists
        return company, None, type(e).__name__


def harvest(scope: str = "priority", ats: str | None = None, early_only: bool = True,
            us_only: bool = True, workers: int = 12,
            progress=None) -> tuple[list[dict], dict]:
    """Poll the registered boards in parallel. Returns (roles, stats).

    scope="priority" polls the ~33 verified boards in a few seconds.
    scope="all" polls 2,272 boards and takes minutes, so it is opt-in.
    """
    blocked, block_rx = load_blocklist()
    companies = [c for c in board_list(scope, ats) if c["slug"] not in blocked]
    roles: list[dict] = []
    stats = {"boards": len(companies), "ok": 0, "failed": 0, "seen": 0,
             "kept": 0, "errors": {}, "top": []}
    per_board = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (company, found, err) in enumerate(pool.map(_poll_one, companies), 1):
            if progress and done % 100 == 0:
                progress(done, len(companies))
            if found is None:
                stats["failed"] += 1
                stats["errors"][err] = stats["errors"].get(err, 0) + 1
                continue
            stats["ok"] += 1
            stats["seen"] += len(found)
            if early_only:
                found = [r for r in found if is_early_career(r["title"], r["description"])]
            if us_only:
                found = [r for r in found if is_us(r["location"])]
            if block_rx:
                found = [r for r in found if not block_rx.search(r["title"])]
            if found:
                per_board.append((company["name"], len(found)))
                roles.extend(found)
    stats["kept"] = len(roles)
    stats["top"] = sorted(per_board, key=lambda x: -x[1])[:12]
    return roles, stats


def split_descriptions(roles: list[dict]) -> dict[str, str]:
    """Pull full descriptions out of the roles so the published JSON stays small.

    A full description runs 5-20 KB. Several hundred of them would make the file
    too heavy for the page to fetch. Scoring happens while the text is still in
    hand; after that the page only needs the tags, the score, and a snippet.
    The full text goes to a local cache so a re-score does not need a re-fetch.
    """
    cache = {}
    for role in roles:
        text = role.pop("description", "")
        if text:
            cache[role["id"]] = text
            role["snippet"] = text[:280]
    return cache


def attach_descriptions(roles: list[dict], cache: dict[str, str]) -> int:
    """Put cached descriptions back, so score_role sees the full text again."""
    hits = 0
    for role in roles:
        text = cache.get(role["id"])
        if text:
            role["description"] = text
            hits += 1
    return hits


def merge(existing: list[dict], incoming: list[dict]) -> tuple[int, int]:
    """Append roles that are new. Never overwrite `found` or application state."""
    by_id = {r["id"]: r for r in existing}
    added = updated = 0
    for role in incoming:
        old = by_id.get(role["id"])
        if not old:
            existing.append(role)
            by_id[role["id"]] = role
            added += 1
            continue
        # refresh only the volatile facts; discovery date and history are sticky
        for field in ("url", "location", "workmode", "description", "tags",
                      "eligibility", "season"):
            if role.get(field):
                old[field] = role[field]
        old["dead"] = False
        updated += 1
    return added, updated
