from homelab_guardian.collectors import mount_collector


def _patch(monkeypatch, *, exists=True, ismount=True):
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: exists)
    monkeypatch.setattr(os.path, "ismount", lambda p: ismount)


def test_mounted_is_ok(monkeypatch):
    _patch(monkeypatch, exists=True, ismount=True)
    c = mount_collector.collect({"mounts": [{"path": "/mnt/nas"}]})[0]
    assert c.status == "ok" and c.evidence["mounted"] is True and c.id == "mount_mnt_nas"


def test_not_mounted_is_critical(monkeypatch):
    _patch(monkeypatch, exists=True, ismount=False)
    c = mount_collector.collect({"mounts": ["/mnt/nas"]})[0]
    assert c.status == "critical" and "is not mounted" in c.summary


def test_missing_dir_is_critical(monkeypatch):
    _patch(monkeypatch, exists=False, ismount=False)
    c = mount_collector.collect({"mounts": [{"path": "/mnt/gone"}]})[0]
    assert c.status == "critical" and "mountpoint directory is missing" in c.summary


def test_not_required_dropped_is_warning(monkeypatch):
    _patch(monkeypatch, exists=True, ismount=False)
    c = mount_collector.collect({"mounts": [{"path": "/mnt/opt", "required": False}]})[0]
    assert c.status == "warning"


def test_missing_path_is_unknown():
    c = mount_collector.collect({"mounts": [{"name": "broken"}]})[0]
    assert c.status == "unknown"


def test_no_mounts_configured_is_quiet():
    assert mount_collector.collect({"mounts": []}) == []
