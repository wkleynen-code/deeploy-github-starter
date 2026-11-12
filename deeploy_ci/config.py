"""deeploy.yaml: what you configure, plus the Deeploy ids that `setup` writes back into it."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass
class Config:
    path: Path
    data: dict

    @classmethod
    def load(cls) -> Config:
        path = Path(os.environ.get("DEEPLOY_CONFIG", "deeploy.yaml"))
        if not path.exists():
            raise SystemExit(f"{path} not found: run deeploy-ci from the folder of your copy")
        return cls(path, yaml.safe_load(path.read_text()) or {})

    @property
    def deeploy(self) -> dict:
        """Where in Deeploy: the workspace, and the ids setup filled in."""
        return self.data["deeploy"]

    @property
    def is_set_up(self) -> bool:
        return all(self.deeploy.get(k) for k in ("workspace_id", "use_case_id", "deployment_id", "report_id"))

    @property
    def use_case(self) -> dict:
        return self.data["use_case"]

    @property
    def tests(self) -> dict[str, list[str]]:
        """Each test, and the controls it proves."""
        return {name: list(controls or []) for name, controls in (self.data.get("tests") or {}).items()}

    @property
    def controls(self) -> list[str]:
        """Every control the tests prove, in the order of deeploy.yaml."""
        return list(dict.fromkeys(c for controls in self.tests.values() for c in controls))

    @property
    def framework_name(self) -> str:
        return self.data["own_framework"]["name"]

    @property
    def own_controls(self) -> dict[str, str]:
        """Your own controls: id -> description."""
        return dict(self.data["own_framework"]["controls"])

    @property
    def approvers(self) -> list[str]:
        return list(self.data.get("approvers") or [])

    @property
    def report_title(self) -> str:
        """The test report's name in Deeploy; the automated check on the controls refers to it."""
        return f"Automated test results: {self.use_case['name']}"

    def time(self, when: datetime | None = None) -> str:
        """A moment in the time zone of deeploy.yaml, e.g. '2026-11-12 10:30 CET'."""
        t = (when or datetime.now(timezone.utc)).astimezone(ZoneInfo(self.data.get("timezone") or "UTC"))
        return f"{t:%Y-%m-%d %H:%M} {t:%Z}"

    def save_ids(self, **ids: str) -> None:
        """Write ids into deeploy.yaml, keeping its comments."""
        text = self.path.read_text()
        for key, value in ids.items():
            text = re.sub(rf'^(\s*{key}:)[ \t]*("[^"]*"|[^\s#]*)', rf'\g<1> "{value}"', text, count=1, flags=re.M)
            self.deeploy[key] = value
        self.path.write_text(text)
