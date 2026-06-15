from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Callable

# Secret resolution is name-based everywhere in Guardian: config keys like
# token_env hold the NAME of a secret. The env provider reads that name from
# the process environment. The bitwarden provider also reads it from
# Bitwarden Secrets Manager by secret key, with the environment always
# winning so local overrides and CI keep working unchanged.


class SecretStore:
    provider = "env"

    def get(self, name: str) -> str | None:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider}

    def preload(self) -> int | None:
        """Eagerly load the backend, returning the number of secrets available
        or None when the provider has no preloadable backend (env)."""
        return None

    @property
    def degraded(self) -> bool:
        """True when the provider hit an error and fell back to env-only."""
        return False


class EnvSecretStore(SecretStore):
    provider = "env"

    def get(self, name: str) -> str | None:
        if not name:
            return None
        return os.getenv(name) or None


class BitwardenSecretStore(SecretStore):
    """Resolves secrets from Bitwarden Secrets Manager via the bws CLI.

    One machine-account access token (in the environment) replaces every
    individual token env var. Secrets are fetched in one `bws secret list`
    call and cached for cache_seconds so --interval mode does not hammer
    the API. Any failure degrades to environment-only resolution with a
    single printed warning; a secrets backend outage must never break a scan.
    """

    provider = "bitwarden"

    def __init__(
        self,
        access_token_env: str = "BWS_ACCESS_TOKEN",
        project_id: str = "",
        cache_seconds: float = 300,
        bws_path: str = "bws",
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.access_token_env = access_token_env or "BWS_ACCESS_TOKEN"
        self.project_id = project_id or ""
        self.cache_seconds = float(cache_seconds)
        self.bws_path = bws_path
        self._runner = runner or subprocess.run
        self._clock = clock
        # Transient bws failures (e.g. a DNS blip at boot) shouldn't poison the
        # whole cache_seconds window — a couple of backed-off retries lets a
        # short outage recover before we fall back to env-only.
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = float(retry_backoff)
        self._sleeper = sleeper
        self._env = EnvSecretStore()
        self._cache: dict[str, str] | None = None
        self._cache_loaded_at: float | None = None
        self._warned = False

    def get(self, name: str) -> str | None:
        if not name:
            return None
        env_value = self._env.get(name)
        if env_value:
            return env_value
        return self._load().get(name)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "access_token_env": self.access_token_env,
            "project_id": self.project_id or None,
            "cache_seconds": self.cache_seconds,
        }

    def preload(self) -> int | None:
        return len(self._load())

    @property
    def degraded(self) -> bool:
        return self._warned

    def _warn(self, message: str) -> None:
        if not self._warned:
            print(f"Bitwarden secrets: {message} Falling back to environment variables only.")
            self._warned = True

    def _load(self) -> dict[str, str]:
        now = self._clock()
        if self._cache is not None and self._cache_loaded_at is not None:
            if now - self._cache_loaded_at < self.cache_seconds:
                return self._cache

        access_token = os.getenv(self.access_token_env, "")
        if not access_token:
            self._warn(f"access token environment variable is not set: {self.access_token_env}.")
            self._cache, self._cache_loaded_at = {}, now
            return self._cache

        command = [self.bws_path, "secret", "list"]
        if self.project_id:
            command.append(self.project_id)
        env = {**os.environ, "BWS_ACCESS_TOKEN": access_token}

        result = None
        last_error = ""
        for attempt in range(self._max_retries + 1):
            try:
                result = self._runner(command, capture_output=True, text=True, timeout=30, env=env)
            except FileNotFoundError:
                # A missing binary won't fix itself on retry — fail fast.
                self._warn(f"bws CLI not found at '{self.bws_path}'. Install it or set secrets.bitwarden.bws_path.")
                self._cache, self._cache_loaded_at = {}, now
                return self._cache
            except (subprocess.SubprocessError, OSError) as exc:
                last_error = f"bws CLI failed to run: {exc}."
                result = None
            else:
                if result.returncode == 0:
                    break
                detail = (result.stderr or result.stdout or "").strip()[:200]
                last_error = f"bws secret list failed (exit {result.returncode}): {detail}."
            # Transient — back off and retry unless this was the last attempt.
            if attempt < self._max_retries:
                self._sleeper(self._retry_backoff * (attempt + 1))

        if result is None or result.returncode != 0:
            self._warn(last_error)
            self._cache, self._cache_loaded_at = {}, now
            return self._cache

        try:
            entries = json.loads(result.stdout)
            secrets = {
                entry["key"]: entry["value"]
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("key"), str)
            }
        except (ValueError, TypeError, KeyError) as exc:
            self._warn(f"could not parse bws output: {exc}.")
            secrets = {}

        self._cache, self._cache_loaded_at = secrets, now
        return self._cache


def build_store(config: dict[str, Any] | None) -> SecretStore:
    config = config or {}
    provider = str(config.get("provider", "env")).lower()
    if provider == "bitwarden":
        bw = config.get("bitwarden", {}) or {}
        return BitwardenSecretStore(
            access_token_env=bw.get("access_token_env", "BWS_ACCESS_TOKEN"),
            project_id=bw.get("project_id", ""),
            cache_seconds=bw.get("cache_seconds", 300),
            bws_path=bw.get("bws_path", "bws"),
        )
    if provider != "env":
        print(f"Unknown secrets provider '{provider}'; using environment variables.")
    return EnvSecretStore()
