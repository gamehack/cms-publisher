from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .errors import PublisherError


TRUE = {"1", "true", "yes"}
FALSE = {"0", "false", "no", ""}


def load_environment(path: str | Path | None = None) -> None:
    if path:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise PublisherError("Environment file must be a regular file")
        load_dotenv(target, override=False)


def enabled(name: str) -> bool:
    raw = os.getenv(name, "false").strip().lower()
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    raise PublisherError(f"{name} must be true or false")


def required(name: str, *, minimum: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < minimum:
        raise PublisherError(f"{name} is missing or invalid")
    return value


def state_path() -> Path:
    return Path(os.getenv("CMS_PUBLISHER_STATE", "/var/lib/cms-publisher/state.sqlite"))


def publishing_enabled(profile: str) -> bool:
    return enabled("CMS_ENABLE_DRAFTS") and enabled("CMS_" + profile.upper() + "_ENABLED")
