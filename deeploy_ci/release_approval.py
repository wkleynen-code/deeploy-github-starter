"""Approval in Deeploy before a release, and marking the release live.

`deeploy-ci gate` (job 3): checks that the evidence in Deeploy is complete, registers this commit as a new version
of the deployment (with the test results for the approver to read) and waits until someone approves it in Deeploy.
`deeploy-ci released VERSION_ID` (job 4, after your deploy): deploys the version in Deeploy, so it shows as live.
"""
from __future__ import annotations

import json
import os
import time

import requests

from .config import Config
from .deeploy_client import DeeployClient, DeeployError, DeeployUnavailable
from .github import Run, notice, set_output, summary
from .test_results import RECORD_FILE

APPROVAL_TIMEOUT_MINUTES = 30


def gate(cfg: Config, args) -> None:
    client, run = DeeployClient(cfg), Run.current()
    if problems := evidence_problems(cfg, client):
        summary(f"## Approval in Deeploy: blocked\n\nThe evidence in Deeploy is not complete: {'; '.join(problems)}. "
                f"Nothing is registered or deployed.\n\n[Use case in Deeploy]({client.use_case_url})")
        notice(f"The evidence in Deeploy is not complete: {'; '.join(problems)}.", title="Blocked", level="error")
        raise SystemExit(1)

    version_id = register_version(cfg, client, run)
    set_output("version_id", version_id)
    url = client.version_url(version_id)
    print(f"Version {version_id[:8]} registered in Deeploy. Approve it there: {url}\n"
          f"  (click Approve, then Deploy; if you only approve, job 4 deploys it)", flush=True)
    notice(f"Open {url} and click Approve, then Deploy. This job waits until it's approved.",
           title="Approve this release in Deeploy")
    wait_for_approval(client, version_id, url)


def evidence_problems(cfg: Config, client: DeeployClient) -> list[str]:
    """What Deeploy itself says: the test report must be complete and the automated checks met."""
    answers = (client.get(f"{client.use_case_path}/documentation").get(cfg.deeploy["report_id"]) or {}).get("0") or {}
    problems = []
    if (filled := sum(1 for answer in answers.values() if answer)) < len(cfg.tests):
        problems.append(f"test report {filled}/{len(cfg.tests)}")
    controls = client.use_case_controls()
    problems += [f"automated check on {abbr} not met" for abbr in cfg.own_controls
                 if abbr in controls and not (client.documentation_check(controls[abbr]["id"]) or {}).get("checked")]
    return problems


def register_version(cfg: Config, client: DeeployClient, run: Run) -> str:
    """Updating a registration deployment with approvers creates a new version that waits for their approval."""
    approvers = client.approver_ids(cfg.approvers)
    before = {v["id"] for v in client.get(f"{client.deployment_path}/versionHistory")}
    try:   # sent once only: a second update would register a second version
        client.python_client.update_registration_deployment(client.deployment_id, {
            "approver_user_ids": approvers,
            "message_to_approvers": f"Release {run.sha} by {run.actor}, pipeline run #{run.number}: {run.url}"[:1000],
            "model_card": model_card(cfg, run)})
    except (requests.Timeout, requests.ConnectionError) as e:   # the version may be registered anyway: look below
        print(f"Deeploy didn't confirm the registration ({type(e).__name__}); looking for the new version")
    for _ in range(60):
        if new := [v for v in client.get(f"{client.deployment_path}/versionHistory") if v["id"] not in before]:
            return new[0]["id"]
        time.sleep(3)
    raise DeeployUnavailable("the new version did not appear in Deeploy")


def model_card(cfg: Config, run: Run) -> str:
    """What the approver reads on the version in Deeploy: the commit, the pipeline run and every test result."""
    card = (f"# Release {run.sha}\n\nRegistered by the CI/CD pipeline. Approve it to let the pipeline deploy it.\n\n"
            f"| | |\n|---|---|\n| Commit | [{run.sha}]({run.commit_url}) |\n| By | {run.actor} |\n"
            f"| Pipeline run | [#{run.number}]({run.url}) |\n| Registered | {cfg.time()} |\n")
    if os.path.exists(RECORD_FILE):   # the test results of job 2
        rows = "\n".join(f"| {t['test']} | {'PASS' if t['status'] == 'passed' else 'FAIL'} | {', '.join(t['controls'])} |"
                         for t in json.load(open(RECORD_FILE))["tests"])
        card += f"\n## Tests in this run\n\n| Test | Result | Proves control |\n|---|---|---|\n{rows}\n"
    return card


def wait_for_approval(client: DeeployClient, version_id: str, url: str) -> None:
    deadline, last_message = time.time() + 60 * APPROVAL_TIMEOUT_MINUTES, 0.0
    while time.time() < deadline:
        for decision in client.get(f"{client.deployment_path}/versions/{version_id}").get("approvals") or []:
            who = f"{decision.get('userFirstName', '')} {decision.get('userLastName', '')}".strip()
            if decision.get("status") == "approved":
                summary(f"## Approval in Deeploy: approved by {who}\n\nVersion `{version_id[:8]}` may go to "
                        f"production; job 4 deploys it.\n\n- [Version in Deeploy]({url})")
                notice(f"Approved by {who}. Job 4 deploys it.", title="Approved in Deeploy")
                return
            if decision.get("status") == "changesRequested":
                summary(f"## Approval in Deeploy: changes requested by {who}\n\n\"{decision.get('comment') or '-'}\". "
                        f"Nothing is deployed.\n\n- [Version in Deeploy]({url})")
                notice(f"{who} requested changes: nothing is deployed. {url}", title="Changes requested in Deeploy",
                       level="error")
                raise SystemExit(1)
        if time.time() - last_message > 60:
            print(f"  waiting for approval in Deeploy: {url}", flush=True)
            last_message = time.time()
        time.sleep(5)
    summary(f"## Approval in Deeploy: no decision within {APPROVAL_TIMEOUT_MINUTES} minutes\n\n"
            f"Nothing is deployed. [Approve the version in Deeploy]({url}) and re-run this job.")
    raise SystemExit(1)


def released(cfg: Config, args) -> None:
    client = DeeployClient(cfg)
    url = client.version_url(args.version_id)
    is_live = lambda: (client.get(client.deployment_path).get("activeVersion") or {}).get("id") == args.version_id
    if not is_live():
        try:
            client.post(f"{client.deployment_path}/versions/{args.version_id}/deploy", json={})
        except DeeployError as e:   # someone already clicked Deploy in Deeploy: then wait until it's live
            if "deploying" not in str(e) and "deployed" not in str(e):
                raise
    for _ in range(60):
        if is_live():
            summary(f"## Live in Deeploy\n\nVersion `{args.version_id[:8]}` is now the live version of the "
                    f"deployment.\n\n- [Version in Deeploy]({url})\n- [Deployment in Deeploy]({client.deployment_url})")
            notice(f"Version {args.version_id[:8]} is live. {client.deployment_url}", title="Deployed and live in Deeploy")
            return
        time.sleep(2)
    notice(f"Version {args.version_id[:8]} is not shown as live in Deeploy yet: {url}", title="Not live yet",
           level="warning")
