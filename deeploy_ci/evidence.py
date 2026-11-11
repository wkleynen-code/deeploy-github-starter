"""`report`: test results (JUnit XML) -> the test report, control statuses and automated checks in Deeploy."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from .config import Config
from .deeploy import Deeploy
from .github import Issues, Run, summary

RECORD_FILE = "deeploy-evidence.json"   # this run's results; upload it as an artifact, `gate` reads it
FAILURE_LABEL = "deeploy-test-failure"
CONTROL_CHANGE_LABEL = "deeploy-control-change"


@dataclass
class Result:
    test: str
    controls: list
    status: str     # passed | failed | skipped | missing
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def read_junit(path: str, tests: dict[str, list[str]]) -> list[Result]:
    """Results of the tests in deeploy.yaml. A test can be named as in the XML, as class.name, or without [params]."""
    found = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        problem = next((el for el in case if el.tag in ("failure", "error")), None)
        status = "failed" if problem is not None else "skipped" if case.find("skipped") is not None else "passed"
        message = "" if problem is None else (problem.get("message") or (problem.text or "").strip())[:300]
        name = case.get("name", "")
        for key in (name, f"{case.get('classname', '')}.{name}", re.sub(r"\[.*\]$", "", name)):
            found.setdefault(key, (status, message))
    return [Result(test, controls, *found.get(test, ("missing", "this test did not run")))
            for test, controls in tests.items()]


def report(cfg: Config, args) -> None:
    run, results = Run.current(), read_junit(args.junit, cfg.tests)
    failed = [r for r in results if not r.passed]
    failing_controls = {c for r in failed for c in r.controls}
    record = {"run": asdict(run), "tests": [asdict(r) for r in results], "controls": {}}
    _save(record)   # saved before talking to Deeploy, so GitHub keeps the results even when Deeploy is down
    answers = {str(i): f"PASSED in run #{run.number} on {cfg.stamp()}, commit {run.sha}. {run.url}" if r.passed else None
               for i, r in enumerate(results)}
    score = f"{len(results) - len(failed)}/{len(results)} passed"

    dp, issues = Deeploy(cfg), Issues()
    report_id = cfg.deeploy.get("report_id")
    if not report_id or not dp.report(report_id):
        raise SystemExit("The test report is not in Deeploy: run deeploy-ci setup")

    # the test report: one line per test, filled in when it passed (Deeploy replaces all answers at once)
    dp.patch(f"{dp.U}/documentationTemplates/{report_id}",
             json={"name": f"{cfg.report_title}: {score} (run #{run.number}, {cfg.stamp()})"})
    dp.patch(f"{dp.U}/documentation", json={report_id: {"0": answers}})

    controls = dp.use_case_controls()
    control_changes = " ".join(i["title"] for i in issues.list(CONTROL_CHANGE_LABEL))
    failure_issues = {i["title"]: i for i in issues.list(FAILURE_LABEL)}
    for abbr in cfg.controls:
        c = controls.get(abbr)
        if c is None:
            print(f"  ! {abbr} is not on the use case (run deeploy-ci check)")
            continue
        if abbr in cfg.own_controls:
            _follow_check(dp, c["id"], report_id, met=not failed)
        # In progress while a test fails, or while a control change is open (the owner completes it once implemented)
        status = "inProgress" if abbr in failing_controls or f"[{abbr}]" in control_changes else "completed"
        if c["status"] != status:
            dp.patch(f"{dp.U}/useCaseControls/{c['id']}", json={"status": status})
        record["controls"][abbr] = {"status": status, "url": dp.control_url(c["id"])}
        _sync_issue(issues, failure_issues, abbr, c, [r for r in failed if abbr in r.controls], run, dp)

    _save(record)
    tests = "\n".join(f"| {r.test} | {'PASS' if r.passed else r.status.upper()} | {', '.join(r.controls)} |" for r in results)
    ctrls = "\n".join(f"| [{a}]({v['url']}) | {v['status']} |" for a, v in record["controls"].items())
    summary(f"## Test evidence in Deeploy: {score}\n\n| Test | Result | Controls |\n|---|---|---|\n{tests}\n\n"
            f"| Control in Deeploy | Status |\n|---|---|\n{ctrls}")


def _save(record: dict) -> None:
    with open(RECORD_FILE, "w") as f:
        json.dump(record, f, indent=2)


def _follow_check(dp: Deeploy, ucc_id: str, report_id: str, met: bool) -> None:
    """Deeploy meets the check while the report is linked to it and complete; link it only when every test passed."""
    check = dp.doc_check(ucc_id)
    if not check:
        return
    linked = any(link.get("controlCheckId") == check["id"]
                 for link in dp.get(f"{dp.U}/useCaseControls/{ucc_id}/checkLinks") or [])
    if met and not linked:
        dp.post(f"{dp.U}/useCaseControls/{ucc_id}/checkLinks", json={
            "controlCheckId": check["id"], "resourceType": "documentationTemplate",
            "useCaseDocumentationTemplateId": report_id})
    elif not met and linked:
        dp.delete(f"{dp.U}/useCaseControls/{ucc_id}/checkLinks/{check['id']}")


def _sync_issue(issues: Issues, open_issues: dict, abbr: str, control: dict, failed: list[Result], run: Run,
                dp: Deeploy) -> None:
    """One issue per failing control, closed again when its tests pass."""
    title = f"[{abbr}] Control not met: tests failed"
    if failed and title not in open_issues:
        lines = "\n".join(f"- **{r.test}**: {r.status}" + (f" ({r.message})" if r.message else "") for r in failed)
        issues.open(title, f"**{abbr} {control['control']['name']}** is *In progress* in Deeploy because run "
                           f"#{run.number} (commit {run.sha}) failed:\n\n{lines}\n\n- Run: {run.url}\n"
                           f"- Control in Deeploy: {dp.control_url(control['id'])}\n\n"
                           f"Closed automatically when the tests pass again.", FAILURE_LABEL)
    elif not failed and title in open_issues:
        issues.close(open_issues[title]["number"], f"Tests pass again in run #{run.number}: {run.url}")
