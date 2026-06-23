"""Shared application state — wires the location registry, run manager, and config
loader together. Stdlib only (no FastAPI), so it's importable by the non-API tests.
"""
from __future__ import annotations

from . import engine
from .locations import LocationManager
from .runs import RunManager


class AppState:
    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self.locations = LocationManager()
        self.runs = RunManager(self)

    def config(self) -> dict:
        """scry's effective config, re-resolved each call so credential/config edits
        are picked up without a restart."""
        return engine.load_config(self.config_path)

    def status(self) -> dict:
        cfg = self.config()
        readiness = engine.provider_readiness(cfg, cfg.get("mode", "fusion"))
        return {
            "has_config": engine.has_config_file(self.config_path),
            "ready": readiness["ready"],
            "providers": readiness["providers"],
            "panel": readiness["panel"],
            "mode": cfg.get("mode", "fusion"),
            "fake_engine": engine.fake_enabled(),
        }
