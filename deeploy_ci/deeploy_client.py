"""DeeployClient: everything deeploy-ci asks Deeploy goes through this class.

Deeploy's official Python client (the `deeploy` package) is used where it can: creating controls, a control
framework and the registration deployment, and registering a new version for approval. What that client doesn't
cover yet (use cases, test reports, evidence, versions and approvals, the audit log) goes to Deeploy's REST API.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config

DOCUMENTATION_CHECK = 13   # Deeploy control check "documentation template completed"


class DeeployUnavailable(Exception):
    """Deeploy did not answer (timeout, connection error, 5xx)."""


class DeeployError(Exception):
    """Deeploy refused the request (4xx): a configuration or permission problem."""


class _RetryAdapter(HTTPAdapter):
    """Retry reads for about a minute. Retry writes only when they never reached Deeploy, so none is applied twice."""

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


class DeeployClient:
    def __init__(self, cfg: Config):
        d = cfg.deeploy
        self.api, self.app = d["api"].rstrip("/"), d["app"].rstrip("/")
        self.workspace_id, self.use_case_id, self.deployment_id = d.get("workspace_id"), d.get("use_case_id"), d.get("deployment_id")
        self.session = requests.Session()
        self.session.mount("https://", _RetryAdapter())
        self._python_client = None
        self._sign_in()

    # --- credentials -----------------------------------------------------------------------------------------------

    def _sign_in(self) -> None:
        """A scoped OAuth credential (the pipeline) or personal keys (you, on your machine)."""
        env = os.environ
        self.oauth = bool(env.get("DEEPLOY_CLIENT_ID") and env.get("DEEPLOY_CLIENT_SECRET"))
        if self.oauth:   # the token lives about 15 minutes; _request gets a new one when it has expired
            self.session.headers.pop("Authorization", None)
            token = self._request("POST", f"{self.api}/oauth2/token", data={"grant_type": "client_credentials"},
                                  auth=(env["DEEPLOY_CLIENT_ID"], env["DEEPLOY_CLIENT_SECRET"]), renew=False)
            self.session.headers["Authorization"] = f"Bearer {token['access_token']}"
        elif env.get("DEEPLOY_ACCESS_KEY") and env.get("DEEPLOY_SECRET_KEY"):
            if env.get("GITHUB_ACTIONS") == "true":
                raise SystemExit("Personal Deeploy keys are refused in GitHub Actions: use a scoped OAuth credential "
                                 "(secrets CLIENT_ID and CLIENT_SECRET in the environment 'deeploy')")
            self.session.auth = (env["DEEPLOY_ACCESS_KEY"], env["DEEPLOY_SECRET_KEY"])
        elif env.get("GITHUB_ACTIONS") == "true":
            raise SystemExit("No Deeploy credentials: add the secrets CLIENT_ID and CLIENT_SECRET to the GitHub "
                             "environment 'deeploy' (Settings → Environments → deeploy → Environment secrets)")
        else:
            raise SystemExit("No Deeploy credentials: put DEEPLOY_ACCESS_KEY and DEEPLOY_SECRET_KEY in .env")

    @property
    def python_client(self):
        """Deeploy's official Python client, signed in with the same credentials."""
        if self._python_client is None:
            from deeploy import Client   # imported here: it is large, and only some commands need it
            env = os.environ
            sign_in = ({"oauth_client_id": env["DEEPLOY_CLIENT_ID"], "oauth_client_secret": env["DEEPLOY_CLIENT_SECRET"]}
                       if self.oauth else {"access_key": env["DEEPLOY_ACCESS_KEY"], "secret_key": env["DEEPLOY_SECRET_KEY"]})
            self._python_client = Client(host=urlparse(self.app).hostname, workspace_id=self.workspace_id, **sign_in)
        return self._python_client

    # --- REST API --------------------------------------------------------------------------------------------------

    def _request(self, method: str, url: str, renew: bool = True, **kwargs):
        for attempt in range(4):
            try:
                response = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.RetryError) as e:
                raise DeeployUnavailable(f"{method} {url}: {type(e).__name__}") from e
            # 401: an expired token (get a new one), or a short hiccup Deeploy sometimes has. Deeploy answers 401
            # before doing anything, so repeating a write after it is safe.
            if response.status_code != 401 or not renew or attempt == 3:
                break
            time.sleep(2 * attempt)
            if self.oauth:
                self._sign_in()
        if response.status_code >= 500:
            raise DeeployUnavailable(f"{method} {url}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise DeeployError(f"Deeploy answered {response.status_code} on {method} {url}: {response.text[:300]}")
        body = response.json() if response.text.strip() else {}
        return body["data"] if isinstance(body, dict) and "data" in body else body

    def get(self, path: str, **kw): return self._request("GET", self.api + path, **kw)
    def post(self, path: str, **kw): return self._request("POST", self.api + path, **kw)
    def patch(self, path: str, **kw): return self._request("PATCH", self.api + path, **kw)
    def delete(self, path: str, **kw): return self._request("DELETE", self.api + path, **kw)

    # API paths of the workspace, the use case and the deployment
    @property
    def workspace_path(self) -> str: return f"/workspaces/{self.workspace_id}"
    @property
    def use_case_path(self) -> str: return f"{self.workspace_path}/useCases/{self.use_case_id}"
    @property
    def deployment_path(self) -> str: return f"{self.workspace_path}/deployments/{self.deployment_id}"

    # --- pages in the Deeploy app, for the links in messages ----------------------------------------------------------

    @property
    def use_case_url(self) -> str:
        return f"{self.app}/workspaces/{self.workspace_id}/useCases/{self.use_case_id}"

    @property
    def deployment_url(self) -> str:
        return f"{self.app}/workspaces/{self.workspace_id}/deployments/{self.deployment_id}"

    def control_url(self, use_case_control_id: str) -> str:
        return f"{self.use_case_url}/useCaseControls/{use_case_control_id}"

    def report_url(self, report_id: str) -> str:
        return f"{self.use_case_url}/templateDocumentation/{report_id}"

    def version_url(self, version_id: str) -> str:
        return f"{self.deployment_url}/versions/{version_id}"

    # --- lookups that several commands need ------------------------------------------------------------------------

    def use_case_controls(self) -> dict[str, dict]:
        """The controls on the use case by id, e.g. {'POL-1': {...}}."""
        return {c["control"]["abbreviation"]: c for c in self.get(f"{self.use_case_path}/useCaseControls")}

    def documentation_check(self, use_case_control_id: str) -> dict | None:
        """The "documentation template completed" check on a control of the use case, if it has one."""
        body = self.get(f"{self.use_case_path}/useCaseControls/{use_case_control_id}/checks")
        checks = (body.get("checks") if isinstance(body, dict) else body) or []
        return next((c for c in checks if c.get("type") == DOCUMENTATION_CHECK), None)

    def report_exists(self, report_id: str) -> bool:
        return any(t["id"] == report_id for t in self.get(f"{self.use_case_path}/documentationTemplates"))

    def approver_ids(self, emails: list[str]) -> list[str]:
        """Deeploy user ids for these e-mail addresses; none given means the owners of the workspace."""
        users = self.get(f"{self.workspace_path}/users")
        if not emails:
            return [u["identity"]["id"] for u in users if u.get("workspaceRole") == "owner"]
        by_email = {(u["identity"]["traits"].get("email") or "").lower(): u["identity"]["id"] for u in users}
        missing = [e for e in emails if e.lower() not in by_email]
        if missing:
            raise DeeployError(f"approvers not in the workspace: {', '.join(missing)}")
        return [by_email[e.lower()] for e in emails]
