"""Controls that someone changed in Deeploy (scenario D).

`deeploy-ci watch` (the daily workflow): reads Deeploy's audit log, which records every change with who made it and
when. For each control of the use case that was changed, it:
  - opens a GitHub issue with the control's text before and after the change
  - sets the control to In progress, with the change and the issue as evidence
The control stays In progress until the issue is closed and someone completes it in Deeploy (`report` respects this).
`deeploy-ci change-control ID TEXT`: changes one of your controls, as your AI lead would in Deeploy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Config
from .deeploy_client import DeeployClient
from .github import Issues, notice, summary
from .test_results import CONTROL_CHANGE_LABEL

LOOK_BACK = timedelta(hours=25)   # the workflow runs daily; an hour of overlap. Changes already in an issue are skipped.
EVIDENCE_NAME = "Control change"   # name of the evidence this adds; replaced on the next change
PAGE_SIZE = 100


def watch(cfg: Config, args) -> None:
    client, issues = DeeployClient(cfg), Issues()
    controls = {c["controlId"]: c for c in client.use_case_controls().values()}
    already_in_an_issue = " ".join(i.get("body") or "" for i in issues.list(CONTROL_CHANGE_LABEL, state="all"))

    edits = {}   # control id -> its edits in the period; several edits count as one change
    since = datetime.now(timezone.utc) - LOOK_BACK
    for log in sorted(read_audit_log(client, since), key=lambda log: log["createdAt"]):
        edited_fields = set(log.get("requestBody") or {}) - {"checks"}   # setup adding the check is no change
        if log["resourceId"] in controls and edited_fields and log["id"] not in already_in_an_issue:
            edits.setdefault(log["resourceId"], []).append(log)

    changed = [handle_change(cfg, client, issues, controls[control_id], logs) for control_id, logs in edits.items()]
    rows = "\n".join(f"| [{abbr}]({url}) | " + (f"[#{i['number']}]({i['html_url']})" if i else "-") + " |"
                     for abbr, url, i in changed)
    summary("## Control changes in Deeploy (last 25 hours)\n\n"
            + (f"Each changed control is now *In progress* in Deeploy, with the change and the issue as evidence.\n\n"
               f"| Changed in Deeploy | GitHub issue |\n|---|---|\n{rows}\n\n[Use case in Deeploy]({client.use_case_url})"
               if changed else f"No control of this use case changed. [Use case in Deeploy]({client.use_case_url})"))


def read_audit_log(client: DeeployClient, since: datetime) -> list[dict]:
    """All control edits in Deeploy's audit log since then (every page)."""
    found, offset = [], 0
    start, end = int(since.timestamp() * 1000), int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000
    while True:
        page = client.get("/logs", params={"logType": "eq:controlEdited", "start": start, "end": end,
                                           "limit": PAGE_SIZE, "offset": offset})
        found += page
        if len(page) < PAGE_SIZE:
            return found
        offset += PAGE_SIZE


def value_before(cfg: Config, client: DeeployClient, control_id: str, abbr: str, field: str, first_edit: dict) -> str:
    """The audit log only holds new values. The value before is in the previous edit of the control; without one,
    it is the text setup created the control with."""
    until = int(datetime.fromisoformat(first_edit["createdAt"].replace("Z", "+00:00")).timestamp() * 1000) - 1
    for log in client.get("/logs", params={"resourceId": f"eq:{control_id}", "logType": "eq:controlEdited",
                                           "start": 0, "end": until, "sort": "createdAt:desc", "limit": 50}):
        if field in (log.get("requestBody") or {}):
            return str(log["requestBody"][field])
    return cfg.own_controls.get(abbr, "(unknown)") if field == "description" else "(unknown)"


def handle_change(cfg: Config, client: DeeployClient, issues: Issues, control: dict, logs: list[dict]):
    abbr, name, use_case_control_id = control["control"]["abbreviation"], control["control"]["name"], control["id"]
    last = logs[-1]
    who = last.get("executedByName") or "someone"
    when = cfg.time(datetime.fromisoformat(last["createdAt"].replace("Z", "+00:00")))
    after = {field: str(value) for field, value in (last.get("requestBody") or {}).items() if field != "checks"}
    before = {field: value_before(cfg, client, control["controlId"], abbr, field, logs[0]) for field in after}

    path = f"{client.use_case_path}/useCaseControls/{use_case_control_id}"
    if control["status"] != "inProgress":
        client.patch(path, json={"status": "inProgress"})

    changes = "\n\n".join(f"**{field}**\n- Before: {before[field]}\n- After: {value}" for field, value in after.items())
    issue = issues.open(
        f"[{abbr}] Control changed in Deeploy: {name}"[:250],
        f"{who} changed **{abbr} {name}** in Deeploy at {when}. Implement it and update the tests that prove it; then "
        f"close this issue and set the control to Completed in Deeploy.\n\n{changes}\n\n"
        f"Control in Deeploy (now *In progress*): {client.control_url(use_case_control_id)}\n\n"
        f"<sub>Deeploy audit log: {', '.join(log['id'] for log in logs)}</sub>", CONTROL_CHANGE_LABEL)

    for evidence in client.get(path).get("evidences") or []:   # replace the evidence of an earlier change
        if (evidence.get("name") or "").startswith(EVIDENCE_NAME):
            client.delete(f"{path}/evidence/{evidence['id']}")
    what = "; ".join(f'{field} changed from "{before[field]}" to "{value}"' for field, value in after.items())
    client.post(f"{path}/evidence", json={
        "name": f"{EVIDENCE_NAME}: In progress until implemented", "evidenceType": "text",
        "source": f"Changed in Deeploy by {who} at {when}: {what}. The engineers implement and test it (see the "
                  f"GitHub issue); the owner then sets this control to Completed."})
    if issue:
        client.post(f"{path}/evidence", json={"name": f"{EVIDENCE_NAME}: GitHub issue #{issue['number']}",
                                              "evidenceType": "link", "source": issue["html_url"]})
    print(f"  {abbr}: In progress" + (f", issue #{issue['number']}" if issue else ""))
    notice(f"{abbr} changed in Deeploy by {who}: now In progress until implemented."
           + (f" Issue #{issue['number']}." if issue else ""), title=f"Control changed: {abbr}")
    return abbr, client.control_url(use_case_control_id), issue


def change_control(cfg: Config, args) -> None:
    client = DeeployClient(cfg)
    control = client.use_case_controls().get(args.control)
    if control is None:
        raise SystemExit(f"{args.control} is not on the use case")
    client.patch(f"/controls/{control['controlId']}", json={"description": args.text})
    print(f"{args.control} changed in Deeploy: {client.control_url(control['id'])}")
    print("Next, in GitHub: the Actions tab → Deeploy control changes → GitHub issues → Run workflow")
