"""GitHub: the Actions run context, the run summary and issues. Works locally too (then it prints)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests


@dataclass
class Run:
    number: str
    url: str
    sha: str
    commit_url: str
    actor: str

    @classmethod
    def current(cls) -> Run:
        e = os.environ.get
        server, repo, run_id, sha = e("GITHUB_SERVER_URL", "https://github.com"), e("GITHUB_REPOSITORY"), \
            e("GITHUB_RUN_ID"), e("GITHUB_SHA")
        return cls(number=e("GITHUB_RUN_NUMBER", "local"),
                   url=f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "(local run)",
                   sha=sha[:7] if sha else "local",
                   commit_url=f"{server}/{repo}/commit/{sha}" if repo and sha else "",
                   actor=e("GITHUB_ACTOR") or e("USER", "local"))


def summary(markdown: str) -> None:
    """Add to the run summary in GitHub Actions; print it as well."""
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(markdown + "\n")
    print(markdown)


def set_output(name: str, value: str) -> None:
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")


class Issues:
    """Issues in the repository of the run. Without GITHUB_TOKEN and GITHUB_REPOSITORY it only prints."""

    def __init__(self):
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        self.enabled = bool(token and repo)
        self.api = f"https://api.github.com/repos/{repo}"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def list(self, label: str, state: str = "open") -> list[dict]:
        if not self.enabled:
            return []
        r = self.session.get(f"{self.api}/issues", params={"labels": label, "state": state, "per_page": 100}, timeout=30)
        r.raise_for_status()
        return r.json()

    def open(self, title: str, body: str, label: str) -> dict | None:
        if not self.enabled:
            print(f"(no GITHUB_TOKEN: would open issue '{title}')")
            return None
        self.session.post(f"{self.api}/labels", json={"name": label}, timeout=30)   # 422 when it exists: fine
        r = self.session.post(f"{self.api}/issues", json={"title": title, "body": body, "labels": [label]}, timeout=30)
        r.raise_for_status()
        return r.json()

    def close(self, number: int, comment: str) -> None:
        if self.enabled:
            self.session.post(f"{self.api}/issues/{number}/comments", json={"body": comment}, timeout=30)
            self.session.patch(f"{self.api}/issues/{number}", json={"state": "closed"}, timeout=30)
