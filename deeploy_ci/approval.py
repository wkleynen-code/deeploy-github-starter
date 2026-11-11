"""`gate`: register the commit as a version in Deeploy and wait for approval. `released`: mark it live."""
from __future__ import annotations

import json
import os
import time

from .config import Config
from .deeploy import Deeploy, DeeployError, DeeployUnavailable
from .evidence import RECORD_FILE
from .github import Run, notice, set_output, summary


def gate(cfg: Config, args) -> None:
    mode = cfg.approval.get("mode", "hard")
    if mode == "off":
        summary("## Approval in Deeploy: off\n\n`approval.mode` is `off` in deeploy.yaml: no version is registered, "
                "and job 4 deploys without approval.")
        set_output("release", "true")
        return
    run = Run.current()
    record = json.load(open(RECORD_FILE)) if os.path.exists(RECORD_FILE) else None
    card = model_card(cfg, run, record)

    dp = Deeploy(cfg)
    if problems := _evidence_problems(cfg, dp):
        summary(f"## Approval in Deeploy: blocked\n\nThe evidence in Deeploy is not complete: {'; '.join(problems)}. "
                f"Nothing is registered or deployed.\n\n[Use case in Deeploy]({dp.use_case_url})")
        raise SystemExit(1)

    before = {v["id"] for v in dp.get(f"{dp.D}/versionHistory")}
    try:   # sent once only: a second PATCH would register a second version
        dp.patch(dp.D, json={"deploymentType": "Registration", "repositoryId": None, "branchName": None,
                             "commit": None, "contractPath": None, "modelCard": card,
                             "approverUserIds": dp.approvers(cfg.approval.get("approvers") or []),
                             "messageToApprovers": f"Release {run.sha} by {run.actor}, run #{run.number}: {run.url}"[:1000]})
    except DeeployUnavailable:
        print("Deeploy is slow to confirm the registration; waiting for the version to appear")
    version = _wait_for_new_version(dp, before)
    set_output("version_id", version["id"])
    url = dp.version_url(version["id"])
    print(f"Version {version['id'][:8]} registered in Deeploy.\n"
          f"  Approve it there: {url}\n"
          f"  (click Approve, then Deploy; if you only approve, job 4 deploys it)", flush=True)
    notice(f"Open {url} and click Approve, then Deploy. This job waits until it's approved.",
           title="Approve this release in Deeploy")

    if mode == "soft":
        summary(f"## Approval in Deeploy: registered for review\n\nVersion `{version['id'][:8]}` is registered in Deeploy "
                f"and asks for approval, but the release continues (`approval.mode: soft`).\n\n"
                f"- [Review the version in Deeploy]({url})\n- [Deployment in Deeploy]({dp.deployment_url})")
        set_output("release", "true")
        return
    _wait_for_decision(cfg, dp, version["id"], url)


def model_card(cfg: Config, run: Run, record: dict | None) -> str:
    """What the approver reads in Deeploy."""
    card = (f"# Release {run.sha}\n\nRegistered by the CI/CD pipeline. Approve it to let the pipeline deploy it.\n\n"
            f"| | |\n|---|---|\n| Commit | [{run.sha}]({run.commit_url}) |\n"
            f"| By | {run.actor} |\n| Pipeline run | [#{run.number}]({run.url}) |\n| Registered | {cfg.stamp()} |\n")
    if record:
        rows = "\n".join(f"| {t['test']} | {'PASS' if t['status'] == 'passed' else 'FAIL'} | {', '.join(t['controls'])} |"
                         for t in record["tests"])
        card += f"\n## Tests in this run\n\n| Test | Result | Controls |\n|---|---|---|\n{rows}\n"
    return card


def _evidence_problems(cfg: Config, dp: Deeploy) -> list[str]:
    """The report must be complete and the automated checks met, as Deeploy sees it now."""
    report_id = cfg.deeploy.get("report_id")
    answers = (dp.get(f"{dp.U}/documentation").get(report_id) or {}).get("0") or {}
    problems = []
    if (filled := sum(1 for v in answers.values() if v)) < len(cfg.tests):
        problems.append(f"test report {filled}/{len(cfg.tests)}")
    controls = dp.use_case_controls()
    problems += [f"automated check on {a} not met" for a in cfg.own_controls
                 if a in controls and not (dp.doc_check(controls[a]["id"]) or {}).get("checked")]
    return problems


def _wait_for_new_version(dp: Deeploy, before: set) -> dict:
    for _ in range(60):
        if new := [v for v in dp.get(f"{dp.D}/versionHistory") if v["id"] not in before]:
            return new[0]
        time.sleep(3)
    raise DeeployUnavailable("the new version did not appear in Deeploy")


def _wait_for_decision(cfg: Config, dp: Deeploy, version_id: str, url: str) -> None:
    minutes = int(cfg.approval.get("timeout_minutes", 30))
    deadline, last_note = time.time() + 60 * minutes, 0.0
    while time.time() < deadline:
        for a in dp.get(f"{dp.D}/versions/{version_id}").get("approvals") or []:
            who = f"{a.get('userFirstName', '')} {a.get('userLastName', '')}".strip()
            if a.get("status") == "approved":
                summary(f"## Approval in Deeploy: approved by {who}\n\nVersion `{version_id[:8]}` may go to production; "
                        f"job 4 deploys it.\n\n- [Version in Deeploy]({url})")
                notice(f"Approved by {who}. Job 4 deploys it.", title="Approved in Deeploy")
                set_output("release", "true")
                return
            if a.get("status") == "changesRequested":
                summary(f"## Approval in Deeploy: changes requested by {who}\n\n\"{a.get('comment') or '-'}\". "
                        f"Nothing is deployed.\n\n- [Version in Deeploy]({url})")
                notice(f"{who} requested changes: nothing is deployed. {url}", title="Changes requested in Deeploy",
                       level="error")
                raise SystemExit(1)
        if time.time() - last_note > 60:
            print(f"  waiting for approval in Deeploy: {url}", flush=True)
            last_note = time.time()
        time.sleep(5)
    summary(f"## Approval in Deeploy: no decision within {minutes} minutes\n\n"
            f"Nothing is deployed. [Approve the version in Deeploy]({url}) and re-run this job.")
    raise SystemExit(1)


def released(cfg: Config, args) -> None:
    """Deploy the approved version in Deeploy, unless someone already clicked Deploy there, and wait until it's live."""
    dp = Deeploy(cfg)
    url = dp.version_url(args.version_id)
    if not any(a.get("status") == "approved" for a in dp.get(f"{dp.D}/versions/{args.version_id}").get("approvals") or []):
        summary(f"## Not marked live in Deeploy\n\nVersion `{args.version_id[:8]}` isn't approved yet (`approval.mode: soft`): "
                f"it stays waiting for review in Deeploy. [Version in Deeploy]({url})")
        return
    live = lambda: (dp.get(dp.D).get("activeVersion") or {}).get("id") == args.version_id
    if not live():
        try:
            dp.post(f"{dp.D}/versions/{args.version_id}/deploy", json={})
        except DeeployError as e:
            if "deploying" not in str(e) and "deployed" not in str(e):
                raise
            print("The version is already being deployed in Deeploy (someone clicked Deploy there).")
    for _ in range(60):
        if live():
            summary(f"## Live in Deeploy\n\nVersion `{args.version_id[:8]}` is now the live version of the deployment.\n\n"
                    f"- [Version in Deeploy]({url})\n- [Deployment in Deeploy]({dp.deployment_url})")
            notice(f"Version {args.version_id[:8]} is live. {dp.deployment_url}", title="Deployed and live in Deeploy")
            return
        time.sleep(2)
    notice(f"Version {args.version_id[:8]} is not shown as live in Deeploy yet: {url}", title="Not live yet", level="warning")
