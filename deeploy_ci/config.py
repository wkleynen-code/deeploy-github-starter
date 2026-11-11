"""deeploy.yaml: what you configure, plus the Deeploy ids that `setup` writes back."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ID_KEYS = ("workspace_id", "use_case_id", "deployment_id", "report_id")


@dataclass
class Config:
    path: Path
    data: dict

    @classmethod
    def load(cls, path: str | None = None) -> Config:
        p = Path(path or os.environ.get("DEEPLOY_CONFIG", "deeploy.yaml"))
        if not p.exists():
            raise SystemExit(f"{p} not found: run from the folder that holds it, or set DEEPLOY_CONFIG")
        return cls(p, yaml.safe_load(p.read_text()) or {})

    @property
    def deeploy(self) -> dict:
        return self.data["deeploy"]

    @property
    def use_case(self) -> dict:
        return self.data["use_case"]

    @property
    def tests(self) -> dict[str, list[str]]:
        """Test name -> the controls it provides evidence for."""
        return {name: list(controls or []) for name, controls in (self.data.get("tests") or {}).items()}

    @property
    def controls(self) -> list[str]:
        """Every control the tests cover, in config order."""
        return list(dict.fromkeys(c for controls in self.tests.values() for c in controls))

    @property
    def own_framework(self) -> dict:
        return self.data.get("own_framework") or {}

    @property
    def own_controls(self) -> dict[str, str]:
        return dict(self.own_framework.get("controls") or {})

    @property
    def approval(self) -> dict:
        return self.data.get("approval") or {}

    @property
    def report_title(self) -> str:
        return f"Automated test results: {self.use_case['name']}"

    def on_deeploy_down(self) -> str:
        """For report and watch; without Deeploy nothing is approved, so nothing is deployed."""
        return self.data.get("on_deeploy_down", "skip")

    def stamp(self, when: datetime | None = None) -> str:
        t = (when or datetime.now(timezone.utc)).astimezone(ZoneInfo(self.data.get("timezone") or "UTC"))
        return f"{t:%Y-%m-%d %H:%M} {t:%Z}"

    def save_ids(self, **ids: str) -> None:
        """Write ids into deeploy.yaml in place, keeping its comments."""
        text = self.path.read_text()
        for key, value in ids.items():
            text = re.sub(rf'^(\s*{key}:)[ \t]*("[^"]*"|[^\s#]*)', rf'\g<1> "{value}"', text, count=1, flags=re.M)
            self.deeploy[key] = value
        self.path.write_text(text)
