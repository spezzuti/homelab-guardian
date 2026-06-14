from homelab_guardian.network_ready import derive_probe_hosts, wait_for_network_ready


def _net_config(**network):
    return {"collectors": {"network": network}}


def test_derive_probe_hosts_pulls_from_all_check_types_and_skips_loopback():
    config = _net_config(
        dns_checks=[{"hostname": "example.com"}, {"hostname": "localhost"}],
        tcp_checks=[{"host": "192.168.0.1", "port": 53}],
        tls_checks=[{"host": "guardian.example.com"}],
        http_checks=[{"url": "https://nas.example.com:9000/healthz"}],
    )
    hosts = derive_probe_hosts(config)
    assert hosts == ["example.com", "192.168.0.1", "guardian.example.com", "nas.example.com"]
    assert "localhost" not in hosts


def test_derive_probe_hosts_dedupes():
    config = _net_config(
        dns_checks=[{"hostname": "a.example.com"}],
        tls_checks=[{"host": "a.example.com"}],
    )
    assert derive_probe_hosts(config) == ["a.example.com"]


def test_ready_returns_true_immediately_when_disabled():
    calls = []
    config = {"app": {"network_ready": {"enabled": False}}, **_net_config(dns_checks=[{"hostname": "x"}])}
    assert wait_for_network_ready(config, resolver=lambda h: calls.append(h)) is True
    assert calls == []  # never probed


def test_ready_returns_true_when_no_probe_hosts():
    assert wait_for_network_ready(_net_config()) is True


def test_ready_returns_true_when_resolver_succeeds():
    config = _net_config(dns_checks=[{"hostname": "example.com"}])
    assert wait_for_network_ready(config, resolver=lambda h: None) is True


def test_ready_retries_then_succeeds():
    attempts = {"n": 0}
    slept = []

    def resolver(host):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("not up yet")
        return None

    config = _net_config(dns_checks=[{"hostname": "example.com"}])
    assert wait_for_network_ready(config, resolver=resolver, sleep=slept.append) is True
    assert len(slept) == 2  # slept twice before the third attempt resolved


def test_ready_times_out_and_proceeds():
    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def sleep(_):
        clock["t"] += 60  # advance the clock so we cross the deadline

    config = {
        "app": {"network_ready": {"timeout_seconds": 100, "interval_seconds": 60}},
        **_net_config(dns_checks=[{"hostname": "down.example.com"}]),
    }
    result = wait_for_network_ready(
        config,
        resolver=lambda h: (_ for _ in ()).throw(OSError("down")),
        sleep=sleep,
        monotonic=monotonic,
        log=lambda m: None,
    )
    assert result is False  # gave up, proceeds with the scan anyway
