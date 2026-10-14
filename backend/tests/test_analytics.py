"""Cookie-free visit analytics: what is (and is not) counted, that nothing personal is stored, attribution,
aggregation and the endpoints. Real schema on a throwaway SQLite database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import analytics as an

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


@pytest.fixture(autouse=True)
def _fresh_counts():
    an._day_counts.clear()
    yield
    an._day_counts.clear()


def hit(path="/", ref="", src="", med="", camp="", ip="1.2.3.4", ua=CHROME, dnt=False, now=NOW):
    return an.record_hit(path, ref, src, med, camp, ip=ip, ua=ua, dnt=dnt, now=now)


def rows(real_db):
    with real_db.engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(select(real_db.page_views_table))]


# -------------------------------------------------------------- what is counted --


def test_a_normal_visit_is_recorded_without_any_personal_data(real_db):
    assert hit("/", ref="https://www.linkedin.com/feed/", ip="203.0.113.7") is True
    (r,) = rows(real_db)
    assert r["path"] == "/" and r["source"] == "linkedin" and r["device"] == "desktop" and r["day"] == "2026-09-25"
    assert "203.0.113.7" not in str(r) and CHROME not in str(r)  # neither the IP nor the user-agent is stored
    assert set(r) == {"id", "day", "ts", "path", "visitor", "source", "medium", "campaign", "device"}


@pytest.mark.parametrize("ua", ["", "Googlebot/2.1", "curl/8.0", "python-requests/2.31", "Mozilla/5.0 HeadlessChrome/120", "WhatsApp/2.23", "UptimeRobot/2.0"])
def test_bots_crawlers_and_monitors_are_not_counted(real_db, ua):
    assert hit(ua=ua) is False and rows(real_db) == []


def test_do_not_track_is_respected(real_db):
    assert hit(dnt=True) is False and rows(real_db) == []


@pytest.mark.parametrize("path", ["/admin", "/admin/strategy", "/api/me/portfolio", "/_next/static/x.js", "/auth/login", "/oauth/complete", "", "no-slash", "/a\x00b"])
def test_private_and_internal_pages_are_never_counted(real_db, path):
    assert hit(path) is False and rows(real_db) == []


def test_paths_drop_query_and_fragment_and_mask_ids(real_db):
    hit("/reports/9f1c2b7a4d3e11ee8a0a0242ac120002?token=secret#x")
    hit("/track-record/?utm_source=x")
    hit("/jobs/123456")
    assert sorted(r["path"] for r in rows(real_db)) == ["/jobs/:id", "/reports/:id", "/track-record"]


# ----------------------------------------------------------------- attribution --


@pytest.mark.parametrize(
    "ref,utm,expected",
    [
        ("https://www.linkedin.com/posts/x", "", "linkedin"),
        ("https://lnkd.in/abc", "", "linkedin"),
        ("https://github.com/Shreyasg13/glassbox-trading-agents", "", "github"),
        ("https://www.google.com/", "", "google"),
        ("https://news.ycombinator.com/item?id=1", "", "hackernews"),
        ("https://t.co/xyz", "", "x"),
        ("https://glassbox-portfolio-review.duckdns.org/login", "", "direct"),  # our own site is not a source
        ("", "", "direct"),
        ("https://some-blog.example/post", "", "some-blog.example"),
        ("https://www.linkedin.com/", "Launch Post!!", "launch-post"),  # an explicit utm_source wins and is sanitised
    ],
)
def test_source_attribution(ref, utm, expected):
    assert an.normalize_source(ref, utm) == expected


def test_campaign_tags_are_sanitised_and_length_capped(real_db):
    hit("/", src="LinkedIn", med="social", camp="Launch <script>alert(1)</script>" + "x" * 200)
    (r,) = rows(real_db)
    assert r["source"] == "linkedin" and r["medium"] == "social"
    assert "<" not in r["campaign"] and len(r["campaign"]) <= an.MAX_TAG


def test_device_classes():
    assert an.device_class(IPHONE) == "mobile" and an.device_class(CHROME) == "desktop"
    assert an.device_class("Mozilla/5.0 (iPad; CPU OS 17_0) Safari") == "tablet"


# ------------------------------------------------------------ the visitor hash --


def test_same_visitor_same_day_is_one_person_but_never_linkable_across_days():
    a1, a2 = an.visitor_id("1.1.1.1", CHROME, "2026-09-25"), an.visitor_id("1.1.1.1", CHROME, "2026-09-25")
    assert a1 == a2 and len(a1) == 16
    assert an.visitor_id("1.1.1.1", CHROME, "2026-09-26") != a1  # the daily salt rotates
    assert an.visitor_id("2.2.2.2", CHROME, "2026-09-25") != a1


# ---------------------------------------------------------------------- summary --


def seed(real_db):
    for i in range(3):  # 3 distinct people on the landing page, one via LinkedIn campaign
        hit("/", ip=f"10.0.0.{i}", ref="https://www.linkedin.com/" if i == 0 else "", src="linkedin" if i == 0 else "", med="social" if i == 0 else "", camp="launch" if i == 0 else "")
    hit("/", ip="10.0.0.0")  # the same person again: one more page view, not one more visitor
    hit("/signup", ip="10.0.0.0")
    hit("/dashboard", ip="10.0.0.0")
    hit("/", ip="10.0.0.9", ua=IPHONE, now=NOW - timedelta(days=1))
    hit("/", ip="10.0.0.9", now=NOW - timedelta(days=60))  # outside a 30-day window


def test_summary_counts_unique_visitors_pageviews_sources_and_funnel(real_db):
    seed(real_db)
    users = [
        {"username": "a", "role": "viewer", "created_at": "2026-09-25T09:00:00+00:00"},
        {"username": "admin", "role": "admin", "created_at": "2026-09-25T09:00:00+00:00"},  # the admin is not a signup
        {"username": "old", "role": "viewer", "created_at": "2026-01-01T09:00:00+00:00"},
    ]
    s = an.summary(30, users, NOW)
    today = s["series"][-1]
    assert today["day"] == "2026-09-25" and today["visitors"] == 3 and today["pageviews"] == 6 and today["signups"] == 1
    assert len(s["series"]) == 30 and s["series"][-2]["visitors"] == 1
    assert s["today"] == {"visitors": 3, "pageviews": 6}
    assert s["totals"]["visitor_days"] == 4 and s["totals"]["signups"] == 1 and s["totals"]["signup_rate"] == pytest.approx(0.25)
    src = {x["source"]: x for x in s["sources"]}
    assert src["linkedin"]["visitors"] == 1 and src["direct"]["visitors"] == 3
    assert s["campaigns"] == [{"source": "linkedin", "medium": "social", "campaign": "launch", "visitors": 1}]
    assert next(p for p in s["pages"] if p["path"] == "/")["visitors"] == 4 and next(p for p in s["pages"] if p["path"] == "/")["pageviews"] == 5
    assert [f["visitors"] for f in s["funnel"]] == [4, 1, 1, 1]
    assert {d["device"]: d["visitors"] for d in s["devices"]} == {"desktop": 3, "mobile": 1}
    assert "visitor-days" in s["note"]


def test_a_longer_window_includes_older_traffic_and_live_now_is_recent_only(real_db):
    seed(real_db)
    assert an.summary(90, [], NOW)["totals"]["visitor_days"] == 5
    assert an.summary(30, [], NOW + timedelta(minutes=10))["live_now"] == 3
    assert an.summary(30, [], NOW + timedelta(hours=2))["live_now"] == 0


def test_an_empty_database_gives_a_valid_zeroed_summary(real_db):
    s = an.summary(7, [], NOW)
    assert len(s["series"]) == 7 and s["totals"]["pageviews"] == 0 and s["totals"]["signup_rate"] is None and s["pages"] == []


def test_prune_removes_only_rows_past_retention(real_db, monkeypatch):
    monkeypatch.setattr(an.random, "random", lambda: 1.0)  # no housekeeping prune inside record_hit, whatever the global RNG state
    hit(now=NOW - timedelta(days=an.RETENTION_DAYS + 5))
    hit(now=NOW)
    assert an.prune(NOW) == 1 and len(rows(real_db)) == 1


def test_the_daily_flood_guard_stops_recording(real_db, monkeypatch):
    monkeypatch.setattr(an, "MAX_ROWS_PER_DAY_PER_WORKER", 2)
    assert [hit(ip=f"9.9.9.{i}") for i in range(4)] == [True, True, False, False]


# --------------------------------------------------------------------- endpoints --


def make_client(real_db, admin=False):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth import TokenPayload, get_current_user
    from app.routers import analytics as routes

    routes._hit_limiter._hits.clear()
    app = FastAPI()
    app.include_router(routes.public_router)
    app.include_router(routes.admin_router)
    if admin:
        app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub="admin", role="admin")
    return TestClient(app)


def test_beacon_endpoint_records_and_always_answers_204(real_db):
    c = make_client(real_db)
    r = c.post("/api/analytics/hit", json={"path": "/", "referrer": "https://github.com/x", "utm_source": "", "utm_campaign": "launch"}, headers={"user-agent": CHROME})
    assert r.status_code == 204 and r.content == b""
    assert rows(real_db)[0]["source"] == "github" and rows(real_db)[0]["campaign"] == "launch"
    # a bot, DNT and Global Privacy Control are all silently ignored (still 204)
    assert c.post("/api/analytics/hit", json={"path": "/"}, headers={"user-agent": "Googlebot"}).status_code == 204
    assert c.post("/api/analytics/hit", json={"path": "/"}, headers={"user-agent": CHROME, "DNT": "1"}).status_code == 204
    assert c.post("/api/analytics/hit", json={"path": "/"}, headers={"user-agent": CHROME, "Sec-GPC": "1"}).status_code == 204
    assert len(rows(real_db)) == 1


def test_beacon_rejects_oversized_input_and_is_rate_limited_without_erroring(real_db):
    c = make_client(real_db)
    assert c.post("/api/analytics/hit", json={"path": "/" + "a" * 400}, headers={"user-agent": CHROME}).status_code == 422
    for _ in range(130):
        assert c.post("/api/analytics/hit", json={"path": "/"}, headers={"user-agent": CHROME}).status_code == 204  # never a 429 to a visitor
    assert len(rows(real_db)) <= 120


def test_only_admins_can_read_the_summary(real_db):
    assert make_client(real_db).get("/api/admin/analytics/summary").status_code in (401, 403)
    ok = make_client(real_db, admin=True).get("/api/admin/analytics/summary?days=7")
    assert ok.status_code == 200 and len(ok.json()["series"]) == 7
    assert make_client(real_db, admin=True).get("/api/admin/analytics/summary?days=0").status_code == 422
