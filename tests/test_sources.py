#!/usr/bin/env python3
"""Guards for the feed parsers, the age cutoff, and the application count.

Run: uv run --with pytest --python 3.12 python -m pytest -q tests/test_sources.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))
import radar  # noqa: E402


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# ------------------------------------------------------------- feed parsers
def test_earlycareerradar_keeps_us_tech_rows_with_direct_links():
    rows = [
        {"company": "NXP", "title": "Embedded ML Intern", "location": "Austin, TX",
         "hub": "Other U.S.", "track": "ML & AI", "mode": "Hybrid",
         "postedAt": _days_ago(2), "applyUrl": "https://nxp.wd3.myworkdayjobs.com/x/1",
         "workAuthorization": ["Not stated"], "closed": False},
        {"company": "Blackstone", "title": "SWE Summer Analyst", "location": "London, UK",
         "hub": "International", "track": "SWE", "postedAt": _days_ago(1),
         "applyUrl": "https://x", "closed": False},
        {"company": "Acme", "title": "Marketing Intern", "location": "NYC",
         "hub": "New York", "track": "Marketing", "postedAt": _days_ago(1),
         "applyUrl": "https://x", "closed": False},
        {"company": "Old Co", "title": "Software Intern", "location": "NYC",
         "hub": "New York", "track": "SWE", "postedAt": _days_ago(3),
         "applyUrl": "https://x", "workAuthorization": ["U.S. Citizenship required"],
         "closed": True},
    ]
    out = radar.parse_earlycareerradar(rows)
    assert [r["company"] for r in out] == ["NXP", "Old Co"]
    nxp = out[0]
    assert nxp["source"] == "earlycareerradar"
    assert nxp["url"] == "https://nxp.wd3.myworkdayjobs.com/x/1"
    assert nxp["posted"] == _days_ago(2)
    assert nxp["workmode"] == "hybrid"
    assert nxp["id"] == radar.role_id("NXP", "Embedded ML Intern")
    assert out[1]["dead"] is True
    assert out[1]["eligibility"]["citizenship"]


def test_zshah_reads_the_keyed_dict_and_closed_flag():
    data = {
        "a:b:1": {"company": "Amazon", "title": "SDE Intern", "location": "Seattle, WA",
                  "url": "https://amazon.jobs/1", "posted_at": _days_ago(5) + "T00:00:00Z",
                  "season": "Summer 2027", "sponsorship": "unknown", "is_open": True},
        "a:b:2": {"company": "Amazon", "title": "SDE Intern, Beijing", "location": "Beijing",
                  "url": "https://amazon.jobs/2", "posted_at": None,
                  "season": "Fall 2026", "sponsorship": "no-sponsorship", "is_open": False},
        "a:b:3": {"company": "Ramp", "title": "SWE Intern", "location": "NYC",
                  "url": "https://x", "posted_at": None, "season": "Summer 2027",
                  "sponsorship": "offers", "is_open": True},
        "a:b:4": {"company": "Lockheed", "title": "SWE Intern", "location": "TX",
                  "url": "https://x", "posted_at": None, "season": "Summer 2027",
                  "sponsorship": "citizens-only", "is_open": True},
    }
    out = radar.parse_zshah(data)
    assert len(out) == 4
    assert out[0]["season"] == "summer-2027" and out[0]["posted"] == _days_ago(5)
    assert out[0]["source"] == "zshah101" and out[0]["dead"] is False
    assert out[0]["eligibility"]["sponsorship"] is None  # unknown stays silent
    assert out[1]["dead"] is True and out[1]["season"] == "fall-2026"
    assert out[1]["eligibility"]["sponsorship"] is False
    assert out[2]["eligibility"]["sponsorship"] is True
    assert out[3]["eligibility"]["citizenship"]


def test_vansh_maps_epoch_dates_seasons_and_sponsorship():
    stamp = int((date.today() - timedelta(days=4)).strftime("%s")) if hasattr(date, "strftime") else 0
    rows = [
        {"company_name": "Rippling", "title": "Frontend Software Engineer Intern",
         "locations": ["New York, NY", "SF"], "url": "https://ats.rippling.com/x",
         "date_posted": stamp, "season": "Winter", "sponsorship": "Offers Sponsorship",
         "active": True, "is_visible": True},
        {"company_name": "Gone", "title": "SWE Intern", "locations": [], "url": "https://x",
         "date_posted": stamp, "season": "Summer", "sponsorship": "Other",
         "active": False, "is_visible": True},
        {"company_name": "Hidden", "title": "SWE Intern", "locations": [], "url": "https://x",
         "date_posted": stamp, "season": "Summer", "sponsorship": "Other",
         "active": True, "is_visible": False},
    ]
    out = radar.parse_vansh(rows)
    assert [r["company"] for r in out] == ["Rippling", "Gone"]
    assert out[0]["season"] == "winter-2027"
    assert out[0]["location"] == "New York, NY, SF"
    assert out[0]["eligibility"]["sponsorship"] is True
    assert out[0]["posted"] is not None
    assert out[1]["dead"] is True


def test_waas_reads_the_inertia_page_and_keeps_interns_only():
    page = ('<div id="app" data-page="{&quot;props&quot;:{&quot;jobs&quot;:['
            '{&quot;id&quot;:1,&quot;title&quot;:&quot;Software Engineering Intern&quot;,'
            '&quot;jobType&quot;:&quot;Intern&quot;,&quot;location&quot;:&quot;SF, CA, US&quot;,'
            '&quot;companyName&quot;:&quot;Rational&quot;,&quot;salary&quot;:&quot;$40/hr&quot;,'
            '&quot;applyUrl&quot;:&quot;https://www.workatastartup.com/jobs/1&quot;},'
            '{&quot;id&quot;:2,&quot;title&quot;:&quot;Senior Engineer&quot;,'
            '&quot;jobType&quot;:&quot;Fulltime&quot;,&quot;location&quot;:&quot;Remote&quot;,'
            '&quot;companyName&quot;:&quot;Hive&quot;,&quot;applyUrl&quot;:&quot;https://x&quot;}'
            ']}}"></div>')
    out = radar.parse_waas(page)
    assert len(out) == 1
    assert out[0]["company"] == "Rational" and out[0]["paid"] is True and out[0]["pay"] == "$40/hr"
    assert radar.parse_waas("<html>no data</html>") == []


def test_harvest_default_scope_polls_feeds_and_skips_jobright(monkeypatch):
    monkeypatch.setattr(radar, "board_list", lambda scope, ats: [])
    seen = []

    def fake_poll(company):
        seen.append(company["ats"])
        return company, [], ""
    monkeypatch.setattr(radar, "_poll_one", fake_poll)
    radar.harvest(scope="priority")
    assert sorted(seen) == sorted(f["ats"] for f in radar.FEEDS)
    assert "jobright" not in seen
    seen.clear()
    radar.harvest(scope="priority", ats="jobright")
    assert seen and set(seen) == {"jobright"}
    seen.clear()
    radar.harvest(scope="priority", ats="earlycareerradar")
    assert seen == ["earlycareerradar"]


# ------------------------------------------------------------- age cutoff
def _role(**kw):
    r = radar._blank_role(id="x", company="X", title="Software Intern", tier="strong",
                          tracks={"swe": 80}, best_track="swe")
    r.update(kw)
    return r


def test_stale_roles_drop_off_unless_applied():
    fresh = _role(id="fresh", posted=_days_ago(10))
    old = _role(id="old", posted=_days_ago(radar.MAX_AGE_DAYS + 1))
    old_but_applied = _role(id="mine", posted=_days_ago(200))
    old_but_applied["application"]["status"] = "rejected"
    undated_old_find = _role(id="undated", posted=None, found=_days_ago(90))
    dead = _role(id="dead", posted=_days_ago(1), dead=True)
    via_jobright = _role(id="jr", posted=_days_ago(1), source="jobright",
                         url="https://jobright.ai/jobs/info/abc?utm_source=1")
    applied_via_jobright = _role(id="jr-mine", posted=_days_ago(1), source="jobright",
                                 url="https://jobright.ai/jobs/info/def")
    applied_via_jobright["application"]["status"] = "applied"
    keep, dropped = radar.prune([fresh, old, old_but_applied, undated_old_find, dead,
                                 via_jobright, applied_via_jobright])
    assert [r["id"] for r in keep] == ["fresh", "mine", "jr-mine"]
    assert dropped == 4


def test_harvest_filters_stale_postings_but_lets_closed_ones_through(monkeypatch):
    rows = [_role(id="a", posted=_days_ago(3)),
            _role(id="b", posted=_days_ago(100)),
            _role(id="c", posted=_days_ago(100), dead=True)]
    monkeypatch.setattr(radar, "board_list", lambda scope, ats: [])
    monkeypatch.setattr(radar, "FEEDS", ({"name": "f", "slug": "f", "ats": "f"},))
    monkeypatch.setattr(radar, "_poll_one", lambda c: (c, rows, ""))
    got, _ = radar.harvest(scope="priority")
    assert [r["id"] for r in got] == ["a", "c"]


def test_merge_marks_a_known_role_dead_and_never_adds_a_dead_stranger():
    existing = [_role(id="k", posted=None, found=_days_ago(5))]
    incoming = [_role(id="k", posted=_days_ago(6), dead=True),
                _role(id="stranger", dead=True)]
    added, updated = radar.merge(existing, incoming)
    assert (added, updated) == (0, 1)
    assert existing[0]["dead"] is True
    assert existing[0]["posted"] == _days_ago(6)  # backfilled, found untouched
    assert existing[0]["found"] == _days_ago(5)


# ------------------------------------------------------ application count
def test_count_applications_counts_every_status_but_none():
    roles = []
    for i, (status, day) in enumerate([("applied", "2026-06-19"), ("rejected", "2026-07-02"),
                                        ("in_progress", "2026-08-01"), ("none", None),
                                        ("offer", "2026-09-01")]):
        r = _role(id=str(i))
        r["application"].update({"status": status, "applied": day})
        roles.append(r)
    c = radar.count_applications(roles)
    assert c["total"] == 4
    assert c["by_status"]["rejected"] == 1 and c["by_status"]["applied"] == 1
    assert (c["first"], c["last"]) == ("2026-06-19", "2026-09-01")
    assert radar.count_applications(roles, since="2026-07-01")["total"] == 3


def test_new_application_is_a_full_role_that_counts():
    r = radar.new_application("Marvell", "Design Verification Intern, BS - Summer 2027",
                              url="https://marvell.wd1.myworkdayjobs.com/x", applied="2026-09-06",
                              resume="hwv")
    assert r["id"] == radar.role_id("Marvell", "Design Verification Intern, BS - Summer 2027")
    assert r["application"]["status"] == "applied" and r["application"]["resume"] == "hwv"
    assert r["season"] == "summer-2027" and r["source"] == "email"
    assert radar.count_applications([r])["total"] == 1
