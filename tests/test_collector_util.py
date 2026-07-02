import time

import pytest

from homelab_guardian.collectors._util import ProbeTimeout, probe_with_timeout


def test_returns_value_within_timeout():
    assert probe_with_timeout(lambda: 42, 1.0) == 42


def test_propagates_exception():
    def boom():
        raise OSError("nope")

    with pytest.raises(OSError):
        probe_with_timeout(boom, 1.0)


def test_raises_on_timeout():
    with pytest.raises(ProbeTimeout):
        probe_with_timeout(lambda: time.sleep(2), 0.1)


def test_zero_timeout_runs_inline():
    assert probe_with_timeout(lambda: "direct", 0) == "direct"
