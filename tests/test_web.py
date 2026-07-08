from homelab_guardian.diff import ScanDiff, diff_scans
from homelab_guardian.models import HealthCheck
from homelab_guardian.web import checks_from_snapshot, overall_of, render_empty_page, render_scan_page


def _check(check_id: str, status: str = "ok", summary: str = "fine") -> HealthCheck:
    return HealthCheck(check_id, check_id.replace("_", " ").title(), status, summary)


def _scan(scan_id: int, *checks: HealthCheck, narrative: str | None = None):
    snapshot = {"app": "Homelab Guardian", "checks": [c.to_dict() for c in checks]}
    if narrative:
        snapshot["narrative"] = narrative
    return (scan_id, "2026-06-12T23:00:00+00:00", snapshot)


def test_checks_from_snapshot_roundtrip():
    original = [_check("a", "critical", "broken"), _check("b")]
    snapshot = {"checks": [c.to_dict() for c in original]}
    restored = checks_from_snapshot(snapshot)
    assert [(c.id, c.status, c.summary) for c in restored] == [
        ("a", "critical", "broken"),
        ("b", "ok", "fine"),
    ]


def test_checks_from_snapshot_skips_garbage():
    snapshot = {"checks": ["not a dict", {"id": "x", "status": "warning"}]}
    restored = checks_from_snapshot(snapshot)
    assert len(restored) == 1
    assert restored[0].status == "warning"


def test_overall_of():
    assert overall_of([]) == "unknown"
    assert overall_of([_check("a")]) == "ok"
    assert overall_of([_check("a"), _check("b", "critical")]) == "critical"


def test_page_contains_status_briefing_and_history():
    scan = _scan(7, _check("svc", "critical", "it broke"), narrative="One thing is broken.\n\nCheck the logs.")
    diff = ScanDiff(previous_scan_id=6, previous_created_at="t")
    older = _scan(6, _check("svc", "ok"))
    page = render_scan_page(scan, diff, history=[scan, older], refresh_seconds=60)
    assert "CRITICAL" in page
    assert "Scan #7" in page
    assert "One thing is broken." in page
    assert "Scan history" in page
    assert 'href="/scan/6"' in page  # older scans link; the current one is highlighted
    assert ">#7</span>" in page
    assert 'http-equiv="refresh"' in page


def test_page_escapes_html_in_check_data():
    evil = HealthCheck("x", "<script>alert(1)</script>", "warning", "summary <b>bold</b>")
    page = render_scan_page(_scan(1, evil), ScanDiff(), history=[], refresh_seconds=0)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert 'http-equiv="refresh"' not in page


def test_page_renders_diff_changes():
    previous = {"checks": [_check("svc", "ok").to_dict()]}
    current = [_check("svc", "warning", "degraded")]
    diff = diff_scans(previous, current, previous_scan_id=1, previous_created_at="t")
    page = render_scan_page(_scan(2, *current), diff, history=[], refresh_seconds=0)
    assert "What changed" in page
    assert "ok →" in page


def test_empty_page_mentions_first_scan():
    page = render_empty_page()
    assert "No scans yet" in page


def test_history_strip_links_every_scan():
    scans = [_scan(i, _check("a")) for i in range(20, 0, -1)]
    page = render_scan_page(scans[0], ScanDiff(), history=scans, refresh_seconds=0)
    assert "Scan history" in page
    assert 'href="/scan/19"' in page
    assert 'href="/scan/1"' in page
    # the current scan is highlighted, not linked
    assert 'href="/scan/20"' not in page and ">#20</span>" in page


def test_groups_render_as_panels_with_tallies():
    checks = [
        HealthCheck("http_a", "Web A", "ok", "answers 200", group="Core services"),
        HealthCheck("tls_a", "Web A cert", "ok", "valid 80 days", group="Core services"),
    ]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert 'data-group="Core services"' in page
    assert "2/2 OK" in page
    assert "answers 200" in page


def test_problem_group_sorts_before_healthy_groups():
    checks = [
        HealthCheck("disk_root", "Root disk", "ok", "26% full", group="Storage"),
        HealthCheck("firewall_host", "Host firewall", "critical", "no firewall", group="Security"),
    ]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert page.index('data-group="Security"') < page.index('data-group="Storage"')
    assert ">CRIT<" in page  # the failing check carries a CRIT chip
    assert "0/1 OK" in page and "1/1 OK" in page


def test_group_worst_of_children_uses_explicit_group_over_id():
    checks = [
        HealthCheck("http_a", "Web A", "ok", "ok", group="Core services"),
        HealthCheck("http_b", "Web B", "warning", "degraded", group="Core services"),
    ]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert 'data-group="Core services"' in page
    assert "1/2 OK" in page
    # the id-fallback "Web services" is unused when an explicit group exists
    assert 'data-group="Web services"' not in page


def test_group_falls_back_to_category_for_ungrouped_checks():
    checks = [HealthCheck("disk_root", "Root disk", "ok", "26% full")]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert 'data-group="Storage"' in page
    assert "26% full" in page


def test_hero_uses_brand_art_with_text_fallback():
    scan = _scan(1, _check("a"))
    page = render_scan_page(scan, ScanDiff(), history=[], refresh_seconds=0)
    assert 'class="hero-art"' not in page
    assert "hero-logo-text" in page  # text nameplate when no logotype art
    assert 'class="ochip"' in page  # the status chip is always present
    branded = render_scan_page(scan, ScanDiff(), history=[], refresh_seconds=0,
                               brand={"hero": "/brand/hero.webp",
                                      "logotype-cut": "/brand/logotype-cut.png"})
    assert '<img class="hero-art" src="/brand/hero.webp"' in branded
    assert '<img class="hero-logo" src="/brand/logotype-cut.png"' in branded
    assert 'class="shell-card"' in branded and 'class="tabs"' in branded


def test_live_refresh_ships_script_with_noscript_fallback():
    scan = _scan(1, _check("a"))
    live = render_scan_page(scan, ScanDiff(), history=[], refresh_seconds=60)
    assert "<noscript><meta http-equiv=" in live  # JS-less clients still refresh
    assert "replaceWith(fresh)" in live  # everyone else gets in-place swaps
    static = render_scan_page(scan, ScanDiff(), history=[], refresh_seconds=0)
    assert "http-equiv" not in static and "replaceWith" not in static


def test_muted_outages_stay_visible_and_counts_carry_sparklines():
    down = HealthCheck("tcp_x", "X host", "critical", "down", group="Infra")
    down.acknowledged = True
    down.ack_note = "known"
    scan = _scan(3, down, _check("a"))
    history = [scan, _scan(2, down, _check("a")), _scan(1, _check("a"))]
    page = render_scan_page(scan, ScanDiff(), history=history, refresh_seconds=0)
    assert "Muted outages" in page  # acked problems are quiet, never invisible
    assert "ACK" in page and "known" in page
    assert '<svg class="spark"' in page  # count tiles trend across history
