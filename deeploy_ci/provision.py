"""`setup` creates (or finds) the example in Deeploy and saves the ids. Safe to run again."""
from __future__ import annotations

import time

from .config import Config
from .deeploy import DOC_CHECK, Deeploy

PENDING = 4   # version status "waiting for approval"


def setup(cfg: Config, args) -> None:
    dp = Deeploy(cfg)
    if not dp.ws:
        workspaces = "\n".join(f"  {w['id']}  {w['name']}" for w in dp.get("/workspaces"))
        raise SystemExit(f"Set deeploy.workspace_id in {cfg.path} to one of your workspaces:\n{workspaces}")
    print(f"Setting up Deeploy workspace '{dp.get(dp.W)['name']}':")

    owner = _owner_id(dp, cfg.use_case.get("owner_email"))
    framework_id = _own_framework(dp, cfg)
    _use_case(dp, cfg, owner)
    controls = _activate_frameworks(dp, cfg, framework_id)
    report_id = _report(dp, cfg)
    _evidence_and_checks(dp, cfg, controls, report_id)
    _approval_rule(dp)
    _deployment(dp, cfg)

    cfg.save_ids(workspace_id=dp.ws, use_case_id=dp.uc, deployment_id=dp.dep, report_id=report_id)
    print(f"\nIds saved in {cfg.path}: commit it. Use case in Deeploy: {dp.use_case_url}")


def _done(what: str, created: bool) -> None:
    print(f"  {'created' if created else 'exists '}  {what}")


def _owner_id(dp: Deeploy, email: str | None) -> str:
    users = dp.get(f"{dp.W}/users")
    owners = [u for u in users if u.get("workspaceRole") == "owner"]
    if email:
        match = [u for u in users if (u["identity"]["traits"].get("email") or "").lower() == email.lower()]
    else:
        match = owners if len(owners) == 1 else []
    if not match:
        raise SystemExit("Set use_case.owner_email (the workspace has several owners): "
                         + ", ".join(u["identity"]["traits"].get("email", "?") for u in owners))
    return match[0]["identity"]["id"]


def _own_framework(dp: Deeploy, cfg: Config) -> str | None:
    """Your own controls, grouped in a framework applied to the workspace."""
    if not cfg.own_controls:
        return None
    name = cfg.own_framework["name"]
    existing = {c["abbreviation"]: c for c in dp.get("/controls")}
    ids = []
    for abbr, text in cfg.own_controls.items():
        control = existing.get(abbr) or dp.post("/controls", json={
            "abbreviation": abbr, "name": text.split(".")[0][:100], "description": text,
            "stage": "production", "scope": "use_case", "category": name})
        _done(f"control {abbr}", abbr not in existing)
        ids.append(control["id"])
    framework = next((f for f in dp.get("/controlFrameworks") if f["name"] == name), None)
    created = framework is None
    if created:
        framework = dp.post("/controlFrameworks", json={"name": name, "description": name,
                                                        "workspaceIds": [dp.ws], "controlIds": ids})
    else:
        current = {c["id"] for c in dp.get(f"/controlFrameworks/{framework['id']}").get("controls") or []}
        if set(ids) - current:
            dp.patch(f"/controlFrameworks/{framework['id']}", json={"controlIds": sorted(current | set(ids))})
    _done(f"framework '{name}'" + (" (every use case in this workspace gets its controls)" if created else ""), created)
    return framework["id"]


def _use_case(dp: Deeploy, cfg: Config, owner: str) -> None:
    uc = cfg.use_case
    if dp.uc:
        _done(f"use case '{dp.get(dp.U)['name']}'", False)
        return
    found = next((u for u in dp.get(f"{dp.W}/useCases") if u["name"] == uc["name"]), None)
    dp.uc = (found or dp.post(f"{dp.W}/useCases", json={
        "name": uc["name"], "description": uc.get("description") or uc["name"], "ownerIds": [owner],
        "riskClassification": uc.get("risk_classification", "unclassified")}))["id"]
    _done(f"use case '{uc['name']}'", found is None)


def _activate_frameworks(dp: Deeploy, cfg: Config, own_framework_id: str | None) -> dict[str, dict]:
    """Switch on the frameworks that hold the controls in deeploy.yaml, if they're not on the use case yet."""
    controls = dp.use_case_controls()
    missing = [a for a in cfg.controls if a not in controls]
    if not missing:
        return controls
    workspace_frameworks = {f["id"]: f["name"] for f in dp.get(f"{dp.W}/controlFrameworks")}
    by_abbr = {c["abbreviation"]: c for c in dp.get("/controls")}
    to_activate = {own_framework_id} if own_framework_id and set(missing) & set(cfg.own_controls) else set()
    for abbr in missing:
        to_activate |= {f["id"] for f in (by_abbr.get(abbr) or {}).get("controlFrameworks") or []
                        if f["id"] in workspace_frameworks}
    for fid in to_activate:
        dp.patch(f"{dp.U}/controlFrameworks/{fid}", json={"active": True})
        _done(f"framework '{workspace_frameworks.get(fid, fid)}' active on the use case", True)
    time.sleep(2)
    controls = dp.use_case_controls()
    if still := [a for a in cfg.controls if a not in controls]:
        print(f"  ! not on the use case: {', '.join(still)}. Check these abbreviations, the use case's "
              f"risk classification, or apply their framework to the workspace.")
    return controls


def _report(dp: Deeploy, cfg: Config) -> str:
    """The test report: a template with one line per test, added to the use case. Returns its id on the use case."""
    template = next((t for t in dp.get(f"{dp.W}/documentationTemplates") if t["name"] == cfg.report_title), None)
    if template is None:
        rows = ["category,question,subtitle"] + [
            f'"Automated tests (latest run)","{test}","Evidence for {", ".join(controls)}. '
            f'Filled in by the pipeline when this test passes; empty when it fails."'
            for test, controls in cfg.tests.items()]
        template = dp.post(f"{dp.W}/documentationTemplates", data={"name": cfg.report_title},
                           files={"file": ("template.csv", "\n".join(rows) + "\n", "text/csv")})
    _done(f"test report ({len(cfg.tests)} tests)", "message" in template)
    instance = lambda: next((i for i in dp.get(f"{dp.U}/documentationTemplates")
                             if i.get("documentationTemplateId") == template["id"]), None)
    if instance() is None:
        dp.post(f"{dp.U}/documentationTemplates", json={"documentationTemplateId": template["id"]})
    return instance()["id"]


def _evidence_and_checks(dp: Deeploy, cfg: Config, controls: dict, report_id: str) -> None:
    """Each covered control gets the report as evidence; your own controls also get the automated check."""
    for abbr in cfg.controls:
        c = controls.get(abbr)
        if c is None:
            continue
        evidence = dp.get(f"{dp.U}/useCaseControls/{c['id']}").get("evidences") or []
        if not any(e.get("useCaseDocumentationTemplateId") == report_id for e in evidence):
            if c["status"] == "completed":   # Deeploy accepts new evidence only on an open control
                dp.patch(f"{dp.U}/useCaseControls/{c['id']}", json={"status": "inProgress"})
            tests = [t for t, cs in cfg.tests.items() if abbr in cs]
            dp.post(f"{dp.U}/useCaseControls/{c['id']}/evidence", json={
                "name": "Automated tests in the CI/CD pipeline", "evidenceType": "text",
                "source": f"Verified on every pipeline run by: {', '.join(tests)}. The linked report has one line per "
                          f"test, filled in when it passes. A failing test sets this control to In progress."})
            dp.post(f"{dp.U}/useCaseControls/{c['id']}/evidence", json={
                "name": cfg.report_title, "evidenceType": "linked_template", "useCaseDocumentationTemplateId": report_id})
            _done(f"evidence on {abbr}", True)
        if abbr in cfg.own_controls:
            check = dp.doc_check(c["id"])
            needed = not check or check.get("selectedOption") != cfg.report_title
            if needed:   # Deeploy shows the template name in the check, so it must be the report's exact title
                dp.patch(f"/controls/{c['controlId']}", json={"checks": [{"type": DOC_CHECK, "selectedOption": cfg.report_title}]})
            _done(f"automated check on {abbr}", needed)


def _approval_rule(dp: Deeploy) -> None:
    if not dp.get(f"{dp.W}/rules/approval"):
        dp.post("/rules/approval", json={"workspaceId": dp.ws, "owner": 1, "operator": 0, "reviewer": 0,
                                         "applyToNewDeployments": True, "applyToDeploymentUpdates": True})
        _done("approval rule (1 workspace owner approves)", True)


def _deployment(dp: Deeploy, cfg: Config) -> None:
    """A 'Registration' deployment: it only holds the approved versions; Deeploy hosts nothing and reads no code."""
    if dp.dep:
        _done(f"deployment '{dp.get(dp.D)['name']}'", False)
        return
    name = f"{cfg.use_case['name']} (releases)"
    found = next((d for d in dp.get(f"{dp.W}/deployments") if d["name"] == name), None)
    if found:
        dp.dep = found["id"]
    else:
        dp.dep = dp.post(f"{dp.W}/deployments", json={
            "name": name, "deploymentType": "Registration", "useCaseId": dp.uc,
            "repositoryId": None, "branchName": None, "commit": None, "contractPath": None,
            "approverUserIds": dp.approvers(cfg.approval.get("approvers") or []),
            "messageToApprovers": "Created by deeploy-ci setup",
            "modelCard": f"# {cfg.use_case['name']}\n\nVersions are registered by the CI/CD pipeline."})["id"]
        time.sleep(2)
        for v in dp.get(f"{dp.D}/versionHistory"):   # the first version needs no approval
            if v.get("status") == PENDING:
                dp.patch(f"{dp.D}/versions/{v['id']}/approvalDecision",
                         json={"approvalStatus": "changesRequested", "comment": "Initial registration by setup"})
    _done(f"deployment '{name}'", found is None)

