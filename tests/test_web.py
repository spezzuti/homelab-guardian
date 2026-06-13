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
    page = render_scan_page(scan, diff, history=[scan], refresh_seconds=60)
    assert "CRITICAL" in page
    assert "Scan #7" in page
    assert "One thing is broken." in page
    assert "Scan history" in page
    assert 'href="/scan/7"' in page
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


def test_history_collapses_after_visible_rows():
    scans = [_scan(i, _check("a")) for i in range(20, 0, -1)]
    page = render_scan_page(scans[0], ScanDiff(), history=scans, refresh_seconds=0)
    assert "Show 15 older scans" in page
    assert 'href="/scan/20"' in page
    assert 'href="/scan/1"' in page


def test_healthy_groups_render_as_collapsible_tiles():
    # All-ok groups render as collapsible tiles in the multi-column grid —
    # dense, and each can be rolled up.
    checks = [
        HealthCheck("http_a", "Web A", "ok", "answers 200", group="Core services"),
        HealthCheck("tls_a", "Web A cert", "ok", "valid 80 days", group="Core services"),
    ]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert "tilegrid" in page
    assert '<details class="tile okc" open>' in page
    assert "Core services" in page
    assert 'title="answers 200"' in page


def test_problem_group_card_renders_before_healthy_tiles():
    # A group with a problem becomes a full-width open card and sorts above the
    # healthy tile grid.
    checks = [
        HealthCheck("disk_root", "Root disk", "ok", "26% full", group="Storage"),
        HealthCheck("firewall_host", "Host firewall", "critical", "no firewall", group="Security"),
    ]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert '<details class="group crit" open>' in page
    # healthy Storage group is now a tile in the grid, below the problem card
    assert "Storage" in page
    assert page.index('class="group crit"') < page.index('<div class="tilegrid">')


def test_group_worst_of_children_uses_explicit_group_over_id():
    # One warning child makes the whole group a warning, and an explicit group
    # overrides the id-derived fallback.
    checks = [
        HealthCheck("http_a", "Web A", "ok", "ok", group="Core services"),
        HealthCheck("http_b", "Web B", "warning", "degraded", group="Core services"),
    ]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert '<details class="group warn" open>' in page
    # the single heading rolls both up; the id-fallback "Web services" is unused
    assert "Web services" not in page


def test_group_falls_back_to_category_for_ungrouped_checks():
    checks = [HealthCheck("disk_root", "Root disk", "ok", "26% full")]
    page = render_scan_page(_scan(1, *checks), ScanDiff(), history=[], refresh_seconds=0)
    assert "Disks" in page
    assert 'title="26% full"' in page
