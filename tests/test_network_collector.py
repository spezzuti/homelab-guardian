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


def test_tcp_unreachable_is_critical_by_default():
    # A target Guardian was told to watch but can't reach at all is "down" —
    # critical by default so it trips the deterministic alert path. Port 1 on
    # localhost is essentially never listening; refusal is immediate.
    checks = network_collector.collect(
        {"tcp_checks": [{"id": "t1", "host": "127.0.0.1", "port": 1, "timeout": 1}]}
    )
    assert checks[0].status == "critical"
    assert "error" in checks[0].evidence


def test_tcp_unreachable_can_opt_out_to_warning():
    checks = network_collector.collect(
        {"tcp_checks": [{"id": "t1", "host": "127.0.0.1", "port": 1, "timeout": 1,
                         "critical_on_unreachable": False}]}
    )
    assert checks[0].status == "warning"


def test_http_check_missing_url_is_unknown():
    checks = network_collector.collect({"http_checks": [{"id": "h1"}]})
    assert checks[0].status == "unknown"


def test_http_unreachable_is_critical_by_default():
    checks = network_collector.collect(
        {"http_checks": [{"id": "h1", "url": "http://127.0.0.1:1", "timeout": 1}]}
    )
    assert checks[0].status == "critical"
    assert checks[0].evidence["expected_status"] == [200]


def test_http_unreachable_can_opt_out_to_warning():
    checks = network_collector.collect(
        {"http_checks": [{"id": "h1", "url": "http://127.0.0.1:1", "timeout": 1,
                          "critical_on_unreachable": False}]}
    )
    assert checks[0].status == "warning"


def test_dns_unresolvable_is_critical_by_default():
    checks = network_collector.collect(
        {"dns_checks": [{"id": "d1", "hostname": "no-such-host.invalid"}]}
    )
    assert checks[0].status == "critical"


def test_dns_unresolvable_can_opt_out_to_warning():
    checks = network_collector.collect(
        {"dns_checks": [{"id": "d1", "hostname": "no-such-host.invalid",
                         "critical_on_unreachable": False}]}
    )
    assert checks[0].status == "warning"


def test_malformed_item_degrades_only_that_check():
    # A single bad numeric value must NOT abort the whole collector and wipe
    # every other target's result — it degrades only its own check to unknown.
    checks = network_collector.collect(
        {
            "tcp_checks": [
                {"id": "bad", "host": "127.0.0.1", "port": "https", "timeout": 1},
                {"id": "good", "host": "127.0.0.1", "port": 1, "timeout": 1},
            ]
        }
    )
    by_id = {c.id: c for c in checks}
    assert by_id["bad"].status == "unknown"
    assert by_id["good"].status == "critical"  # port 1 refused = reachable check still ran


def test_http_invalid_expected_status_is_unknown():
    checks = network_collector.collect(
        {"http_checks": [{"id": "h1", "url": "http://127.0.0.1:1", "expected_status": "abc"}]}
    )
    assert checks[0].status == "unknown"


def test_tls_invalid_port_is_unknown():
    checks = network_collector.collect(
        {"tls_checks": [{"id": "t1", "host": "example.com", "port": "https"}]}
    )
    assert checks[0].status == "unknown"
