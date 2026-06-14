from homelab_guardian.collectors import network_collector


def test_no_checks_configured_reports_nothing():
    # Enabled-but-unconfigured is calm by default — the guidance lives in
    # `guardian doctor` (preflight), not as noise on the live dashboard.
    assert network_collector.collect({}) == []


def test_dns_check_missing_hostname_is_unknown():
    checks = network_collector.collect({"dns_checks": [{"id": "d1", "name": "broken"}]})
    assert checks[0].status == "unknown"


def test_tcp_check_missing_port_is_unknown():
    checks = network_collector.collect({"tcp_checks": [{"id": "t1", "host": "localhost"}]})
    assert checks[0].status == "unknown"


def test_tcp_unreachable_is_warning():
    # Port 1 on localhost is essentially never listening; refusal is immediate.
    checks = network_collector.collect(
        {"tcp_checks": [{"id": "t1", "host": "127.0.0.1", "port": 1, "timeout": 1}]}
    )
    assert checks[0].status == "warning"
    assert "error" in checks[0].evidence


def test_http_check_missing_url_is_unknown():
    checks = network_collector.collect({"http_checks": [{"id": "h1"}]})
    assert checks[0].status == "unknown"


def test_http_unreachable_is_warning():
    checks = network_collector.collect(
        {"http_checks": [{"id": "h1", "url": "http://127.0.0.1:1", "timeout": 1}]}
    )
    assert checks[0].status == "warning"
    assert checks[0].evidence["expected_status"] == [200]
