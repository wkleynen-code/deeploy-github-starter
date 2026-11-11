"""A small client for the Deeploy API: authentication, retries and the paths this tool uses."""
from __future__ import annotations

import os
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config

DOC_CHECK = 13   # control check type "documentation template completed"


class DeeployUnavailable(Exception):
    """Deeploy did not answer (timeout, connection error, 5xx)."""


class DeeployError(Exception):
    """Deeploy refused the request (4xx): a configuration or permission problem."""


class _RetryAdapter(HTTPAdapter):
    """Reads retry for about a minute; writes only when they never reached Deeploy, so nothing is applied twice."""

    def send(self, request, *args, **kwargs):
        if request.method == "GET":
            kwargs["timeout"] = kwargs.get("timeout") or 30
            self.max_retries = Retry(total=6, backoff_factor=1, allowed_methods=["GET"],
                                     status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False)
        else:
            kwargs["timeout"] = kwargs.get("timeout") or 120
            self.max_retries = Retry(total=3, connect=3, read=0, status=0, other=0, backoff_factor=1,
                                     allowed_methods=False)
        return super().send(request, *args, **kwargs)


class Deeploy:
    def __init__(self, cfg: Config):
        d = cfg.deeploy
        self.api, self.app = d["api"].rstrip("/"), d["app"].rstrip("/")
        self.ws, self.uc, self.dep = d.get("workspace_id"), d.get("use_case_id"), d.get("deployment_id")
        self.session = requests.Session()
        self.session.mount("https://", _RetryAdapter())
        self._authenticate()

    def _authenticate(self) -> None:
        env = os.environ
        self.oauth = bool(env.get("DEEPLOY_CLIENT_ID") and env.get("DEEPLOY_CLIENT_SECRET"))
        if self.oauth:   # scoped OAuth credential: a short-lived token (about 15 minutes), renewed when it expires
            self.session.headers.pop("Authorization", None)
            token = self._request("POST", f"{self.api}/oauth2/token", data={"grant_type": "client_credentials"},
                                  auth=(env["DEEPLOY_CLIENT_ID"], env["DEEPLOY_CLIENT_SECRET"]), retry_auth=False)
            self.session.headers["Authorization"] = f"Bearer {token['access_token']}"
        elif env.get("DEEPLOY_ACCESS_KEY") and env.get("DEEPLOY_SECRET_KEY"):   # personal keys: local use only
            if env.get("GITHUB_ACTIONS") == "true":
                raise SystemExit("Personal Deeploy keys are refused in GitHub Actions: use a scoped OAuth credential "
                                 "(secrets CLIENT_ID and CLIENT_SECRET in the environment 'deeploy')")
            self.session.auth = (env["DEEPLOY_ACCESS_KEY"], env["DEEPLOY_SECRET_KEY"])
        elif env.get("GITHUB_ACTIONS") == "true":
            raise SystemExit("No Deeploy credentials: add the secrets CLIENT_ID and CLIENT_SECRET to the GitHub environment "
                             "'deeploy' (Settings → Environments → deeploy → Environment secrets)")
        else:
            raise SystemExit("No Deeploy credentials: set DEEPLOY_ACCESS_KEY and DEEPLOY_SECRET_KEY "
                             "(or DEEPLOY_CLIENT_ID and DEEPLOY_CLIENT_SECRET), e.g. in .env")

    def _request(self, method: str, url: str, retry_auth: bool = True, **kwargs):
        for attempt in range(4):
            try:
                r = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.RetryError) as e:
                raise DeeployUnavailable(f"{method} {url}: {type(e).__name__}") from e
            # 401: an expired token (renew it), or a brief hiccup Deeploy sometimes has. Writes are only
            # repeated after a 401, which Deeploy answers before doing anything, so nothing is applied twice.
            if r.status_code != 401 or not retry_auth or attempt == 3:
                break
            time.sleep(2 * attempt)
            if self.oauth:
                self._authenticate()
        if r.status_code >= 500:
            raise DeeployUnavailable(f"{method} {url}: HTTP {r.status_code}")
        if r.status_code >= 400:
            raise DeeployError(f"Deeploy answered {r.status_code} on {method} {url}: {r.text[:300]}")
        body = r.json() if r.text.strip() else {}
        return body["data"] if isinstance(body, dict) and "data" in body else body

    def get(self, path: str, **kw): return self._request("GET", self.api + path, **kw)
    def post(self, path: str, **kw): return self._request("POST", self.api + path, **kw)
    def patch(self, path: str, **kw): return self._request("PATCH", self.api + path, **kw)
    def delete(self, path: str, **kw): return self._request("DELETE", self.api + path, **kw)

    # API paths of the workspace, use case and deployment
    @property
    def W(self) -> str: return f"/workspaces/{self.ws}"
    @property
    def U(self) -> str: return f"{self.W}/useCases/{self.uc}"
    @property
    def D(self) -> str: return f"{self.W}/deployments/{self.dep}"

    # pages in the Deeploy app, for links in messages
    def control_url(self, use_case_control_id: str) -> str:
        return f"{self.app}/workspaces/{self.ws}/useCases/{self.uc}/useCaseControls/{use_case_control_id}"

    @property
    def use_case_url(self) -> str:
        return f"{self.app}/workspaces/{self.ws}/useCases/{self.uc}"

    @property
    def deployment_url(self) -> str:
        return f"{self.app}/workspaces/{self.ws}/deployments/{self.dep}"

    def report_url(self, report_id: str) -> str:
        return f"{self.use_case_url}/templateDocumentation/{report_id}"

    def version_url(self, version_id: str) -> str:
        """The version page in Deeploy, where the approver reviews and approves it."""
        return f"{self.app}/workspaces/{self.ws}/deployments/{self.dep}/versions/{version_id}"

    # lookups used by several commands
    def use_case_controls(self) -> dict[str, dict]:
        """The controls on the use case by abbreviation, e.g. {'POL-1': {...}}."""
        return {c["control"]["abbreviation"]: c for c in self.get(f"{self.U}/useCaseControls")}

    def doc_check(self, use_case_control_id: str) -> dict | None:
        body = self.get(f"{self.U}/useCaseControls/{use_case_control_id}/checks")
        checks = (body.get("checks") if isinstance(body, dict) else body) or []
        return next((c for c in checks if c.get("type") == DOC_CHECK), None)

    def report(self, report_id: str) -> dict | None:
        return next((t for t in self.get(f"{self.U}/documentationTemplates") if t["id"] == report_id), None)

    def approvers(self, emails: list[str]) -> list[str]:
        """User ids for the given e-mail addresses; no addresses = the workspace owners."""
        users = self.get(f"{self.W}/users")
        if not emails:
            return [u["identity"]["id"] for u in users if u.get("workspaceRole") == "owner"]
        by_email = {(u["identity"]["traits"].get("email") or "").lower(): u["identity"]["id"] for u in users}
        missing = [e for e in emails if e.lower() not in by_email]
        if missing:
            raise DeeployError(f"approvers not in the workspace: {', '.join(missing)}")
        return [by_email[e.lower()] for e in emails]

