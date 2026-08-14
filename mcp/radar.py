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
from datetime import date, datetime, timedelta
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


SCOPES = ("priority", "all", "workday", "everything")


def board_list(scope: str = "priority", ats: str | None = None) -> list[dict]:
    """Which boards a harvest polls.

    priority   ~33 fast JSON boards + 41 curated Workday employers  (~5 min)
    all        every greenhouse/ashby/lever/smartrecruiters board    (~7 min)
    workday    all 1,710 Workday tenants                            (~25 min)
    everything both of the above

    Workday is separated because it costs far more per board: the list endpoint
    carries no description, so each surviving role needs its own detail GET.
    """
    reg = load_boards()
    rows = reg["companies"]
    fast = [c for c in rows if c["ats"] != "workday"]
    wday = [c for c in rows if c["ats"] == "workday"]

    if scope == "priority":
        wanted = set(reg.get("priority") or [])
        keys = {(w["slug"], w["site"]) for w in reg.get("workday_priority") or []}
        rows = ([c for c in fast if c["slug"] in wanted]
                + [c for c in wday if (c["slug"], c.get("site")) in keys])
    elif scope == "all":
        rows = fast
    elif scope == "workday":
        rows = wday
    elif scope != "everything":
        raise ValueError(f"scope must be one of {SCOPES}")

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

# Disciplines that are not software, ML, or hardware verification. A title that
# names one of these is rejected UNLESS it also carries an on-track signal, so
# "Manufacturing Software Engineer Intern" survives and "Civil Engineering
# Intern" does not.
# Two groups, because a trailing \b after a prefix never matches: "pharmac\b"
# cannot match "Pharmacy". PREFIX entries match the stem plus any suffix; EXACT
# entries are whole words.
OFF_TRACK_PREFIX = (
    "civil", "structural", "geotechnical", "environmental", "chemical",
    "petroleum", "mining", "biomedical", "bioengineer", "biolog", "biochem",
    "chemistr", "agronom", "agricultur", "mechanic", "manufactur", "industrial",
    "packaging", "welding", "architect", "construction", "surveying",
    "market", "sales", "recruit", "journalis", "editorial", "copywrit",
    "photograph", "legal", "paralegal", "complian", "audit", "accounting",
    "bookkeep", "payroll", "nurs", "clinical", "pharmac", "dental", "veterinar",
    "radiolog", "phlebotom", "therap", "teach", "tutor", "curriculum",
    "admission", "underwrit", "actuar", "warehouse", "logistic", "procurement",
    "facilit", "janitor", "culinar", "hospitality", "retail", "cashier",
    "barista", "sponsorship", "philanthrop", "fundrais",
)
OFF_TRACK_EXACT = (
    "hr", "human resources", "communications", "public relations", "social media",
    "graphic design", "graphic designer", "fashion", "interior design",
    "real estate", "supply chain", "claims", "tax", "content", "video",
    "process", "finance", "financial", "bridge", "inspector", "planning",
    "business development", "customer success", "customer service",
    "customer experience", "customer support", "talent",
)
OFF_TRACK_RE = re.compile(
    r"\b(?:" + "|".join(OFF_TRACK_PREFIX) + r")\w*"
    r"|\b(?:" + "|".join(OFF_TRACK_EXACT) + r")\b", re.I)

# Any of these in the title rescues an otherwise off-track match, so
# "Manufacturing Software Engineer Intern" survives.
ON_TRACK_RE = re.compile(
    r"\b(software|computer science|\bcs\b|developer|programming|"
    r"data (science|scientist|engineer|analytics)|machine learning|\bml\b|"
    r"\bai\b|artificial intelligence|deep learning|\bnlp\b|\bllm\b|"
    r"firmware|hardware|silicon|\brtl\b|\bfpga\b|\basic\b|\bsoc\b|"
    r"verification|validation engineer|embedded|electrical|\beda\b|"
    r"backend|back-end|frontend|front-end|full.?stack|platform|infrastructure|"
    r"devops|\bsre\b|site reliability|cloud|cyber ?security|"
    r"\bqa\b|test automation|compiler|robotics|quantitative|\bquant\b|"
    r"systems engineer|information technology)\b",
    re.I)


def is_on_track(title: str) -> bool:
    """Reject a discipline that has nothing to do with the three resumes."""
    low = title or ""
    return not OFF_TRACK_RE.search(low) or bool(ON_TRACK_RE.search(low))
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

# ------------------------------------------------------------------------- pay
# An unpaid or credit-only internship must never reach the board. "Stipend" and
# "academic credit" are NOT unpaid signals on their own: a stipend is pay, and
# many paid internships also offer credit.
UNPAID_RE = re.compile(
    r"\bunpaid\b|\bvolunteer\b|\bpro\s*bono\b|without\s+(pay|compensation)"
    r"|no\s+(pay|compensation|salary)\b|\bfor\s+(academic\s+)?credit\s+only\b"
    r"|(course|academic|school|college)\s+credit\s+only|unpaid\s+intern"
)
PAID_RE = re.compile(
    r"\$\s*\d|\bper\s+hour\b|\bhourly\b|\bsalary\b|\bcompensation\b"
    r"|\bpay\s+(range|rate)\b|\bpaid\s+intern|\bstipend\b|\busd\b"
    r"|\bbase\s+pay\b|\bwage\b"
)
# "$45.00 - $60.00 per hour", "$30/hr", "$8,000 - $12,000 per month"
PAY_RANGE_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:-|–|to)\s*\$?\s?([\d,]+(?:\.\d{2})?)"
    r"\s*(?:per\s+|/\s*|a\s+)?(hour|hr|month|mo|year|yr|week|wk)?",
    re.I)
PAY_SINGLE_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:per\s+|/\s*|a\s+)(hour|hr|month|mo|year|yr|week|wk)",
    re.I)
UNIT = {"hr": "hour", "mo": "month", "yr": "year", "wk": "week"}


def pay_of(text: str) -> tuple[bool | None, str]:
    """Returns (paid, pay_range). paid: True | False | None when the posting is
    silent. Most postings are silent, so None must not be treated as unpaid."""
    low = text.lower()
    if UNPAID_RE.search(low):
        return False, ""
    m = PAY_RANGE_RE.search(text) or PAY_SINGLE_RE.search(text)
    if m:
        g = m.groups()
        unit = g[-1] or ""
        unit = UNIT.get(unit.lower(), unit.lower())
        rng = f"${g[0]}" + (f" – ${g[1]}" if len(g) == 3 and g[1] else "")
        return True, (rng + (f" / {unit}" if unit else "")).strip()
    if PAID_RE.search(low):
        return True, ""
    return None, ""


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
# Workday: every company is its own tenant, so a board needs three fields —
# slug, wd host number, and site name. All three ride in data/boards.json.
# The list endpoint filters server-side on searchText, which is what makes this
# affordable: without it a sweep would pull every posting a company has.
WD_TERMS = ("intern", "co-op")
WD_LIMIT = 20
WD_MAX_PAGES = 6          # 120 hits per term per board
WD_DETAIL_CAP = 40        # detail GETs per board; the list gives no description
WD_REL_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?)", re.I)


def _post_json(url: str, payload: dict, timeout: int = 30):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _wd_posted(relative: str | None) -> str | None:
    """Workday's list endpoint gives 'Posted 11 Days Ago', not a date. The detail
    endpoint gives a real startDate, so this is only the fallback."""
    if not relative:
        return None
    m = WD_REL_RE.search(relative)
    if not m:
        return None
    if m.group(1).lower() == "today":
        days = 0
    elif m.group(1).lower() == "yesterday":
        days = 1
    else:
        days = int(m.group(2))
    return (date.today() - timedelta(days=days)).isoformat()


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


# The same employer arrives under different names from different sources —
# "GE Vernova" from JobRight, "Gevernova" from its Workday tenant. Without this,
# one role lands on the board twice.
COMPANY_NOISE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|plc|"
    r"gmbh|nv|sa|ag|holdings?|group|technologies|technology|solutions|systems|"
    r"labs?|the)\b\.?", re.I)


def _norm_company(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", COMPANY_NOISE.sub(" ", company or "").lower())


def role_id(company: str, title: str) -> str:
    """Stable slug. Survives a URL change and a name variant, so the spreadsheet
    and the database can both point at it."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{_norm_company(company)}-{slug}"[:90] or "unknown"


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
    if any(w in low for w in SENIOR_WORDS):
        return False
    return is_on_track(title or "")


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
def _pay_kw(desc: str) -> dict:
    paid, pay = pay_of(desc)
    return {"paid": paid, "pay": pay}


def _blank_role(**kw) -> dict:
    role = {
        "id": "", "company": "", "title": "", "location": "", "workmode": "unspecified",
        "season": None, "url": "", "source": "manual", "posted": None, "found": today(),
        "description": "", "paid": None, "pay": "",
        "eligibility": {"sponsorship": None, "citizenship": None, "class_year": []},
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
            tags=tags_of(f"{title} {desc}"), **_pay_kw(desc),
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
            eligibility=eligibility_of(desc), tags=tags_of(f"{title} {desc}"), **_pay_kw(desc),
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


# JobRight publishes its own daily-refreshed repos. That is the supported route:
# it needs no login, no scraping and no email, and the listed URLs already carry
# JobRight's utm parameters, so following them sends the credit back to them.
# Only the last 7 days are listed, which is exactly the freshness window we want.
JOBRIGHT_REPOS = (
    "2026-Software-Engineer-Internship",
    "2026-Engineer-Internship",
    "2026-Data-Analysis-Internship",
)
JOBRIGHT_RAW = "https://raw.githubusercontent.com/jobright-ai/{repo}/master/README.md"
# | **[Company](site)** | **[Title](jobright url)** | Location | Work Model | Aug 14 |
JR_ROW_RE = re.compile(
    r"^\|\s*\*\*\[(?P<company>[^\]]+)\]\([^)]*\)\*\*\s*"
    r"\|\s*\*\*\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)\*\*\s*"
    r"\|\s*(?P<location>[^|]*)"
    r"\|\s*(?P<mode>[^|]*)"
    r"\|\s*(?P<posted>[^|]*)\|", re.M)
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _jr_date(text: str) -> str | None:
    """'Aug 14' -> ISO. The table has no year, but it only lists the last 7 days,
    so a date in the future must belong to last year."""
    parts = (text or "").strip().split()
    if len(parts) != 2 or parts[0] not in MONTHS:
        return None
    try:
        day = int(parts[1])
    except ValueError:
        return None
    today = date.today()
    try:
        d = date(today.year, MONTHS[parts[0]], day)
    except ValueError:
        return None
    if d > today:
        d = date(today.year - 1, MONTHS[parts[0]], day)
    return d.isoformat()


def fetch_jobright(repo: str, display: str = "") -> list[dict]:
    req = urllib.request.Request(JOBRIGHT_RAW.format(repo=repo),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        md = r.read().decode("utf-8", "replace")

    out = []
    for m in JR_ROW_RE.finditer(md):
        company = m.group("company").strip()
        title = m.group("title").strip()
        loc = m.group("location").strip()
        mode = m.group("mode").strip().lower()
        out.append(_blank_role(
            id=role_id(company, title), company=company, title=title, location=loc,
            workmode=("remote" if "remote" in mode else "hybrid" if "hybrid" in mode
                      else "onsite" if "site" in mode or "office" in mode else "unspecified"),
            season=season_of(title), url=m.group("url").strip(), source="jobright",
            posted=_jr_date(m.group("posted")),
            # the table carries no description, so scoring falls back to the title
            description="", eligibility=eligibility_of(title), tags=tags_of(title),
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
            eligibility=eligibility_of(desc), tags=tags_of(f"{title} {desc}"), **_pay_kw(desc),
        ))
    return out


def fetch_workday(board: str, display: str = "", wd: str = "", site: str = "") -> list[dict]:
    base = f"https://{board}.{wd}.myworkdayjobs.com/wday/cxs/{board}/{site}"
    company = display or board

    # 1. list pages, filtered server-side, de-duplicated across search terms
    listed: dict[str, dict] = {}
    for term in WD_TERMS:
        for page in range(WD_MAX_PAGES):
            try:
                data = _post_json(f"{base}/jobs", {
                    "appliedFacets": {}, "limit": WD_LIMIT,
                    "offset": page * WD_LIMIT, "searchText": term})
            except Exception:
                break
            posts = data.get("jobPostings") or []
            for j in posts:
                if j.get("externalPath"):
                    listed[j["externalPath"]] = j
            if len(posts) < WD_LIMIT:
                break

    # 2. drop what the title and location already rule out, before paying for details
    cheap = [(path, j) for path, j in listed.items()
             if is_early_career(j.get("title") or "")
             and is_us(j.get("locationsText") or "")]

    # 3. only now fetch descriptions, since the list endpoint carries none
    def _detail(item):
        path, j = item
        try:
            return path, j, _get_json(base + path).get("jobPostingInfo") or {}
        except Exception:
            return path, j, {}

    out = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for path, j, info in pool.map(_detail, cheap[:WD_DETAIL_CAP]):
            title = info.get("title") or j.get("title") or ""
            desc = _plain(info.get("jobDescription"))
            loc = info.get("location") or j.get("locationsText") or ""
            country = ((info.get("country") or {}).get("descriptor") or "")
            if country and "united states" not in country.lower():
                continue
            out.append(_blank_role(
                id=role_id(company, title), company=company, title=title, location=loc,
                workmode=_workmode(f"{info.get('remoteType') or ''} {loc} {desc[:400]}"),
                season=season_of(f"{title} {desc[:1500]}"),
                url=info.get("externalUrl") or f"https://{board}.{wd}.myworkdayjobs.com/{site}{path}",
                source="workday",
                posted=info.get("startDate") or _wd_posted(j.get("postedOn")),
                description=desc, eligibility=eligibility_of(desc),
                tags=tags_of(f"{title} {desc}"), **_pay_kw(desc),
            ))
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "jobright": fetch_jobright,
}


def _poll_one(company: dict) -> tuple[dict, list[dict] | None, str]:
    """Poll one board. Never raises: a dead board must not stop the sweep."""
    try:
        fn = FETCHERS[company["ats"]]
        if company["ats"] == "workday":
            return company, fn(company["slug"], company.get("name", ""),
                               company.get("wd", ""), company.get("site", "")), ""
        return company, fn(company["slug"], company.get("name", "")), ""
    except Exception as e:  # network, JSON, or a board that no longer exists
        return company, None, type(e).__name__


def harvest(scope: str = "priority", ats: str | None = None, early_only: bool = True,
            us_only: bool = True, paid_only: bool = True, workers: int = 12,
            progress=None) -> tuple[list[dict], dict]:
    """Poll the registered boards in parallel. Returns (roles, stats).

    scope="priority" polls the ~33 verified boards in a few seconds.
    scope="all" polls 2,272 boards and takes minutes, so it is opt-in.
    """
    blocked, block_rx = load_blocklist()
    companies = [c for c in board_list(scope, ats) if c["slug"] not in blocked]
    if scope in ("priority", "everything") and ats in (None, "", "jobright"):
        companies += [{"name": f"JobRight {r}", "slug": r, "ats": "jobright"}
                      for r in JOBRIGHT_REPOS]
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
            if paid_only:
                # only drop what says it is unpaid; `None` means the posting is
                # silent, and dropping those would empty the board.
                found = [r for r in found if r.get("paid") is not False]
            if found:
                per_board.append((company["name"], len(found)))
                roles.extend(found)
    stats["kept"] = len(roles)
    stats["top"] = sorted(per_board, key=lambda x: -x[1])[:12]
    return roles, stats


def prune(roles: list[dict]) -> tuple[list[dict], int]:
    """Drop harvested roles that scored below the stretch floor.

    They are noise the page never shows, and at ~1,100 rows they were 60% of
    roles.json. Spreadsheet history and anything applied to is kept whatever it
    scores: that is your record, not a recommendation.
    """
    keep = [r for r in roles
            if r.get("tier") != "none"
            or r.get("source") == "sheet"
            or (r.get("application") or {}).get("status", "none") != "none"]
    return keep, len(roles) - len(keep)


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
                      "eligibility", "season", "pay"):
            if role.get(field):
                old[field] = role[field]
        if role.get("paid") is not None:
            old["paid"] = role["paid"]
        old["dead"] = False
        updated += 1
    return added, updated
