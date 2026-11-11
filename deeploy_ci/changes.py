"""`watch`: control and framework changes in Deeploy (its audit log) -> GitHub issues, control In progress.
`change-control`: change one of your own controls from the command line, as your AI lead would in Deeploy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Config
from .deeploy import Deeploy
from .evidence import CONTROL_CHANGE_LABEL
from .github import Issues, summary

CHANGE_EVIDENCE = "Control change"   # name prefix of the evidence watch adds; replaced on the next change


def watch(cfg: Config, args) -> None:
    dp, issues = Deeploy(cfg), Issues()
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=args.minutes)
    logs = dp.get("/logs", params={"limit": 200, "start": int(since.timestamp() * 1000),
                                   "end": int(now.timestamp() * 1000) + 60_000})
    handled = " ".join(i.get("body") or "" for i in issues.list(CONTROL_CHANGE_LABEL, state="all"))
    new = [log for log in sorted(logs, key=lambda x: x["createdAt"])
           if log["id"] not in handled and log["createdAt"] >= since.isoformat()[:23]]   # Deeploy's filter is loose

    controls = {c["controlId"]: c for c in dp.use_case_controls().values()}
    frameworks = {f["id"]: f["name"] for c in controls.values() for f in c["control"].get("controlFrameworks") or []}
    control_edits, framework_edits = {}, {}
    for log in new:
        changed = set(log.get("requestBody") or {})
        if log["logType"] == "controlEdited" and log["resourceId"] in controls and changed - {"checks"}:
            control_edits.setdefault(log["resourceId"], []).append(log)        # several edits = one change
        elif log["logType"] == "controlFrameworkEdited" and log["resourceId"] in frameworks and "controlIds" in changed:
            framework_edits.setdefault(log["resourceId"], []).append(log)

    made = [_control_changed(cfg, dp, issues, controls[cid], group) for cid, group in control_edits.items()]
    made += [_framework_changed(cfg, dp, issues, frameworks[fid], group, controls) for fid, group in framework_edits.items()]
    rows = "\n".join(f"| [{what}]({url}) | " + (f"[#{i['number']}]({i['html_url']})" if i else "-") + " |"
                     for what, url, i in made)
    summary(f"## Control changes in Deeploy (last {args.minutes} min)\n\n"
            + (f"Each changed control is now *In progress* in Deeploy, with the change and the issue as evidence.\n\n"
               f"| Changed in Deeploy | GitHub issue |\n|---|---|\n{rows}\n\n[Use case in Deeploy]({dp.use_case_url})"
               if made else f"No control of this use case changed. [Use case in Deeploy]({dp.use_case_url})"))


def _who_when(cfg: Config, logs: list[dict]) -> tuple[str, str, str]:
    log = logs[-1]
    when = cfg.stamp(datetime.fromisoformat(log["createdAt"].replace("Z", "+00:00")))
    return log.get("executedByName") or "someone", when, ", ".join(x["id"] for x in logs)


def _control_changed(cfg: Config, dp: Deeploy, issues: Issues, c: dict, logs: list[dict]):
    """The control goes to In progress, gets an issue, and the reason plus the issue as evidence."""
    abbr, name, ucc = c["control"]["abbreviation"], c["control"]["name"], c["id"]
    who, when, log_ids = _who_when(cfg, logs)
    changes = "\n".join(f"- **{k}**: {v}" for k, v in (logs[-1].get("requestBody") or {}).items() if k != "checks")
    if c["status"] != "inProgress":
        dp.patch(f"{dp.U}/useCaseControls/{ucc}", json={"status": "inProgress"})
        c["status"] = "inProgress"
    issue = issues.open(
        f"[{abbr}] Control changed in Deeploy: {name}"[:250],
        f"{who} changed **{abbr} {name}** in Deeploy at {when}. Implement it and update the tests that cover it; then "
        f"close this issue and complete the control in Deeploy.\n\nNew values:\n{changes}\n\n"
        f"Control in Deeploy (now *In progress*): {dp.control_url(ucc)}\n\n<sub>Deeploy audit log: {log_ids}</sub>",
        CONTROL_CHANGE_LABEL)
    for e in dp.get(f"{dp.U}/useCaseControls/{ucc}").get("evidences") or []:
        if (e.get("name") or "").startswith(CHANGE_EVIDENCE):
            dp.delete(f"{dp.U}/useCaseControls/{ucc}/evidence/{e['id']}")
    new_values = "; ".join(f"new {k}: \"{v}\"" for k, v in (logs[-1].get("requestBody") or {}).items() if k != "checks")
    dp.post(f"{dp.U}/useCaseControls/{ucc}/evidence", json={
        "name": f"{CHANGE_EVIDENCE}: In progress until implemented", "evidenceType": "text",
        "source": f"Changed in Deeploy by {who} at {when}: {new_values}. The engineers implement and test it (see the "
                  f"GitHub issue); the owner then sets this control to Completed."})
    if issue:
        dp.post(f"{dp.U}/useCaseControls/{ucc}/evidence", json={
            "name": f"{CHANGE_EVIDENCE}: GitHub issue #{issue['number']}", "evidenceType": "link", "source": issue["html_url"]})
    print(f"  {abbr}: In progress" + (f", issue #{issue['number']}" if issue else ""))
    return abbr, dp.control_url(ucc), issue


def _framework_changed(cfg: Config, dp: Deeploy, issues: Issues, framework: str, logs: list[dict], controls: dict):
    """Controls were added to or removed from a framework this use case uses: list them with their status here."""
    who, when, log_ids = _who_when(cfg, logs)
    all_controls = {c["id"]: c for c in dp.get("/controls")}
    lines = []
    for cid in logs[-1]["requestBody"]["controlIds"]:
        c, here = all_controls.get(cid, {}), controls.get(cid)
        where = f"[{here['status']} on this use case]({dp.control_url(here['id'])})" if here else "not on this use case"
        lines.append(f"- **{c.get('abbreviation', cid)}** {c.get('name', '')}: {where}")
    issue = issues.open(
        f"[{framework}] Framework changed in Deeploy"[:250],
        f"{who} changed the controls of **{framework}** in Deeploy at {when}. Controls new to this use case show "
        f"*notStarted*: implement them and cover them with tests in deeploy.yaml.\n\nControls in the framework now:\n"
        + "\n".join(lines) + f"\n\n<sub>Deeploy audit log: {log_ids}</sub>", CONTROL_CHANGE_LABEL)
    print(f"  framework '{framework}' changed" + (f", issue #{issue['number']}" if issue else ""))
    return framework, dp.use_case_url, issue


def change_control(cfg: Config, args) -> None:
    dp = Deeploy(cfg)
    c = dp.use_case_controls().get(args.control)
    if c is None:
        raise SystemExit(f"{args.control} is not on the use case")
    dp.patch(f"/controls/{c['controlId']}", json={"description": args.text})
    print(f"{args.control} changed in Deeploy. Next: deeploy-ci watch")
