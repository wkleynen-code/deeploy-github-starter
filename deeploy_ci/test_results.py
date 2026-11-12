"""Test results, from the JUnit XML file your test run writes.

`deeploy-ci test-summary` (job 1): the results in the run summary in GitHub, with the control each test proves.
`deeploy-ci report` (job 2): the results into Deeploy:
  - the test report gets one line per test, filled in when the test passed and empty when it failed
  - the automated check on your controls is met only while every test passes
  - each control's status follows its tests: In progress when one fails, Completed when they all pass
  - GitHub gets an issue for each control whose tests fail; it closes when they pass again
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from .config import Config
from .deeploy_client import DeeployClient
from .github import Issues, Run, notice, summary

RECORD_FILE = "deeploy-evidence.json"   # the results of this run, kept as a GitHub artifact; job 3 reads it
TEST_FAILURE_LABEL = "deeploy-test-failure"
CONTROL_CHANGE_LABEL = "deeploy-control-change"
STATUS = {"completed": "Completed", "inProgress": "In progress", "notStarted": "Not started"}


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
    """The results of the tests in deeploy.yaml (by test name; parametrized tests as one, without [params])."""
    found = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        problem = next((el for el in case if el.tag in ("failure", "error")), None)
        status = "failed" if problem is not None else "skipped" if case.find("skipped") is not None else "passed"
        message = "" if problem is None else (problem.get("message") or (problem.text or "").strip())[:300]
        found.setdefault(re.sub(r"\[.*\]$", "", case.get("name", "")), (status, message))
    return [Result(test, controls, *found.get(test, ("missing", "this test did not run")))
            for test, controls in tests.items()]


def test_summary(cfg: Config, args) -> None:
    results = read_junit(args.junit, cfg.tests)
    failed = [r for r in results if not r.passed]
    rows = "\n".join(f"| {r.test} | {'PASS' if r.passed else r.status.upper()} | {', '.join(r.controls)} |"
                     + (f" {r.message} |" if failed else "") for r in results)
    header = "| Test | Result | Proves control |" + (" Why it failed |" if failed else "")
    summary(f"## Tests: {len(results) - len(failed)}/{len(results)} passed\n\n"
            f"The tests of the AI system, and the Deeploy control each one proves.\n\n"
            f"{header}\n|---|---|---|{'---|' if failed else ''}\n{rows}\n\n"
            + ("Next: job 2 writes these results into Deeploy. Jobs 3 and 4 don't run, because a test failed."
               if failed else "Next: job 2 writes these results into Deeploy, then job 3 asks for approval there."))
    for r in failed:
        notice(f"{r.message or r.status}. It proves {', '.join(r.controls)}: nothing is deployed.",
               title=f"Test failed: {r.test}", level="error")
    if not failed:
        notice(f"All {len(results)} tests passed. They prove {', '.join(cfg.controls)}.", title="Tests passed")


def report(cfg: Config, args) -> None:
    run, results = Run.current(), read_junit(args.junit, cfg.tests)
    failed = [r for r in results if not r.passed]
    failing_controls = {c for r in failed for c in r.controls}
    record = {"run": asdict(run), "tests": [asdict(r) for r in results], "controls": {}}
    save_record(record)   # first, so GitHub keeps the results even when Deeploy doesn't answer
    score = f"{len(results) - len(failed)}/{len(results)} passed"

    client, issues = DeeployClient(cfg), Issues()
    report_id = cfg.deeploy["report_id"]
    if not client.report_exists(report_id):
        raise SystemExit("The test report isn't on the use case in Deeploy: run deeploy-ci setup")

    # 1. The test report: one answer per test. Deeploy replaces all answers at once, so failed tests are emptied.
    answers = {str(i): f"PASSED in run #{run.number} on {cfg.time()}, commit {run.sha}. {run.url}" if r.passed else None
               for i, r in enumerate(results)}
    suffix = f": {score} (run #{run.number}, {cfg.time()})"   # Deeploy allows names of at most 100 characters
    client.patch(f"{client.use_case_path}/documentationTemplates/{report_id}",
                 json={"name": cfg.report_title[:100 - len(suffix)] + suffix})
    client.patch(f"{client.use_case_path}/documentation", json={report_id: {"0": answers}})

    # 2. The controls: their check and status follow the tests, and GitHub gets an issue per failing control.
    controls = client.use_case_controls()
    open_changes = " ".join(i["title"] for i in issues.list(CONTROL_CHANGE_LABEL))
    open_failures = {i["title"]: i for i in issues.list(TEST_FAILURE_LABEL)}
    for abbr in cfg.controls:
        control = controls.get(abbr)
        if control is None:
            print(f"  ! {abbr} is not on the use case in Deeploy (run deeploy-ci setup)")
            continue
        if abbr in cfg.own_controls:
            update_check(client, control["id"], report_id, all_passed=not failed)
        # In progress while one of its tests fails, or while a change of the control isn't implemented yet
        status = "inProgress" if abbr in failing_controls or f"[{abbr}]" in open_changes else "completed"
        if control["status"] != status:
            client.patch(f"{client.use_case_path}/useCaseControls/{control['id']}", json={"status": status})
        check = (client.documentation_check(control["id"]) or {}).get("checked") if abbr in cfg.own_controls else None
        record["controls"][abbr] = {"status": status, "check": check, "url": client.control_url(control["id"])}
        update_issue(issues, open_failures, abbr, control, [r for r in failed if abbr in r.controls], run, client)
    save_record(record)

    tests = "\n".join(f"| {r.test} | {'PASS' if r.passed else r.status.upper()} | {', '.join(r.controls)} |" for r in results)
    rows = "\n".join(f"| [{a}]({v['url']}) | {STATUS.get(v['status'], v['status'])} | "
                     f"{'-' if v['check'] is None else 'met' if v['check'] else 'not met'} |" for a, v in record["controls"].items())
    summary(f"## Test evidence in Deeploy: {score}\n\n"
            f"The results of this run are in Deeploy: the [test report]({client.report_url(report_id)}) has one line per "
            f"test (filled in when it passed), and each control's status follows its tests.\n\n"
            f"| Test | Result | Proves control |\n|---|---|---|\n{tests}\n\n"
            f"| Control in Deeploy | Status | Automated check |\n|---|---|---|\n{rows}\n\n"
            f"[Use case in Deeploy]({client.use_case_url}) · the full record is in the `{RECORD_FILE}` artifact of this run")
    notice(f"Test report {score}. " + ", ".join(f"{a}: {STATUS.get(v['status'])}" for a, v in record["controls"].items())
           + f". {client.use_case_url}", title="Evidence written to Deeploy")


def save_record(record: dict) -> None:
    with open(RECORD_FILE, "w") as f:
        json.dump(record, f, indent=2)


def update_check(client: DeeployClient, use_case_control_id: str, report_id: str, all_passed: bool) -> None:
    """Deeploy marks the check as met while the test report is complete and linked to the check. So: link the
    report when every test passed, and unlink it when one failed."""
    check = client.documentation_check(use_case_control_id)
    if not check:
        return
    path = f"{client.use_case_path}/useCaseControls/{use_case_control_id}/checkLinks"
    linked = any(link.get("controlCheckId") == check["id"] for link in client.get(path) or [])
    if all_passed and not linked:
        client.post(path, json={"controlCheckId": check["id"], "resourceType": "documentationTemplate",
                                "useCaseDocumentationTemplateId": report_id})
    elif not all_passed and linked:
        client.delete(f"{path}/{check['id']}")


def update_issue(issues: Issues, open_issues: dict, abbr: str, control: dict, failed: list[Result], run: Run,
                 client: DeeployClient) -> None:
    """One issue per control whose tests fail; it closes when they pass again."""
    title = f"[{abbr}] Control not met: tests failed"
    if failed and title not in open_issues:
        lines = "\n".join(f"- **{r.test}**: {r.status}" + (f" ({r.message})" if r.message else "") for r in failed)
        issues.open(title, f"**{abbr} {control['control']['name']}** is *In progress* in Deeploy because run "
                           f"#{run.number} (commit {run.sha}) failed:\n\n{lines}\n\n- Run: {run.url}\n"
                           f"- Control in Deeploy: {client.control_url(control['id'])}\n\n"
                           f"Closed automatically when the tests pass again.", TEST_FAILURE_LABEL)
    elif not failed and title in open_issues:
        issues.close(open_issues[title]["number"], f"Tests pass again in run #{run.number}: {run.url}")
