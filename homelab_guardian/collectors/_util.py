from __future__ import annotations

import threading
from typing import Callable, TypeVar

# Shared collector helpers.
#
# The important one is `probe_with_timeout`: a filesystem call against a dropped
# NFS/CIFS mount can block in the kernel for minutes, uninterruptibly — and a
# dropped mount is exactly what the mount/backup collectors exist to detect. A
# plain `os.stat()` there would hang the whole scan (and, in serve mode, the
# background scan thread). We run the probe in a daemon thread and give up on it
# after a bounded wait: a stuck syscall can't be cancelled, but the daemon
# thread dies with the process and the scan keeps going.

T = TypeVar("T")


class ProbeTimeout(Exception):
    """A filesystem probe did not return within its timeout (likely a hung mount)."""


def probe_with_timeout(func: Callable[[], T], timeout: float) -> T:
    """Run `func()` in a daemon thread and return its result, raising
    ProbeTimeout if it does not finish within `timeout` seconds. Exceptions
    raised by `func` propagate unchanged. `timeout <= 0` disables the bound and
    calls `func` directly (mainly so tests can opt out of the thread hop)."""
    if timeout <= 0:
        return func()

    box: dict[str, object] = {}

    def worker() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True, name="guardian-fs-probe")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise ProbeTimeout(f"filesystem probe did not return within {timeout:g}s")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]
