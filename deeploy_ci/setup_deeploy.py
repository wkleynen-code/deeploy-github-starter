"""`deeploy-ci setup`: create the example in Deeploy and save the ids in deeploy.yaml.

It creates (or reuses, when it exists) everything the pipeline needs, in this order:
the test report, your controls with their automated check, your control framework, the use case, the controls'
evidence, an approval rule, the deployment for the versions, and the use case's lifecycle stage.
Running it again changes nothing that is already there.
"""
from __future__ import annotations

import contextlib
import io
import time

from .config import Config
from .deeploy_client import DOCUMENTATION_CHECK, DeeployClient, DeeployError

WAITING_FOR_APPROVAL = 4   # version status
LIFECYCLE = ["exploration", "development", "validation", "production"]   # Deeploy moves one stage at a time


def setup(cfg: Config, args) -> None:
    client = DeeployClient(cfg)
    if not client.workspace_id:
        workspaces = "\n".join(f"  {w['id']}  {w['name']}" for w in client.get("/workspaces"))
        raise SystemExit(f"Put the id of your test workspace in {cfg.path} as workspace_id, then run setup again:\n"
                         f"{workspaces}")
    print(f"Setting up Deeploy workspace '{client.get(client.workspace_path)['name']}':")

    owner_id = find_owner(client, cfg.use_case.get("owner_email"))
    template_id = create_report_template(client, cfg)
    control_ids = create_own_controls(client, cfg)
    create_framework(client, cfg, control_ids)
    create_use_case(client, cfg, owner_id)
    report_id = add_report_to_use_case(client, template_id)
    add_evidence_to_controls(client, cfg, report_id)
    create_approval_rule(client)
    create_deployment(client, cfg)
    in_production = move_to_production(client)

    cfg.save_ids(workspace_id=client.workspace_id, use_case_id=client.use_case_id,
                 deployment_id=client.deployment_id, report_id=report_id)
    print_overview(client, cfg, report_id, in_production)


def done(what: str, created: bool) -> None:
    print(f"  {'created' if created else 'exists '}  {what}")


def find_owner(client: DeeployClient, email: str | None) -> str:
    """The use case needs an owner: the only owner of the workspace, or the one in use_case.owner_email."""
    users = client.get(f"{client.workspace_path}/users")
    owners = [u for u in users if u.get("workspaceRole") == "owner"]
    if email:
        owners = [u for u in users if (u["identity"]["traits"].get("email") or "").lower() == email.lower()]
    if len(owners) != 1:
        raise SystemExit("Set use_case.owner_email in deeploy.yaml to one of the workspace's owners")
    return owners[0]["identity"]["id"]


def create_report_template(client: DeeployClient, cfg: Config) -> str:
    """The test report is a documentation template with one question per test (uploaded as a CSV file)."""
    template = next((t for t in client.get(f"{client.workspace_path}/documentationTemplates")
                     if t["name"] == cfg.report_title), None)
    if template is None:
        rows = ["category,question,subtitle"] + [
            f'"Automated tests (latest run)","{test}","Proves {", ".join(controls)}. Filled in by the pipeline '
            f'when this test passes; empty when it fails."' for test, controls in cfg.tests.items()]
        template = client.post(f"{client.workspace_path}/documentationTemplates", data={"name": cfg.report_title},
                               files={"file": ("template.csv", "\n".join(rows) + "\n", "text/csv")})
    done(f"test report ({len(cfg.tests)} tests)", "message" in template)
    return template["id"]


def create_own_controls(client: DeeployClient, cfg: Config) -> list[str]:
    """Your controls, each with the automated check "documentation template completed" on the test report.

    Controls belong to the whole organization, so existing ones are reused (and given the check if needed)."""
    check = {"type": DOCUMENTATION_CHECK, "selected_option": cfg.report_title}   # Deeploy shows this name in the check
    existing = {c["abbreviation"]: c for c in client.get("/controls")}
    new = [{"abbreviation": abbr, "name": text.split(".")[0][:100], "description": text, "stage": "production",
            "scope": "use_case", "category": cfg.framework_name, "checks": [check]}
           for abbr, text in cfg.own_controls.items() if abbr not in existing]
    with contextlib.redirect_stdout(io.StringIO()):   # the Python client prints a line per control
        created = {c.abbreviation: c.id for c in client.python_client.create_controls(new)} if new else {}
    for abbr, control in existing.items():
        if abbr in cfg.own_controls and not any(c.get("type") == DOCUMENTATION_CHECK and
                                                c.get("selectedOption") == cfg.report_title for c in control.get("checks") or []):
            client.patch(f"/controls/{control['id']}",
                         json={"checks": [{"type": DOCUMENTATION_CHECK, "selectedOption": cfg.report_title}]})
    for abbr in cfg.own_controls:
        done(f"control {abbr}, with the automated check", abbr in created)
    return [created.get(abbr) or existing[abbr]["id"] for abbr in cfg.own_controls]


def create_framework(client: DeeployClient, cfg: Config, control_ids: list[str]) -> None:
    """A framework groups your controls. Applied to the workspace, its controls appear on the use cases there."""
    framework = next((f for f in client.get("/controlFrameworks") if f["name"] == cfg.framework_name), None)
    if framework is None:
        client.python_client.create_control_framework({"name": cfg.framework_name, "description": cfg.framework_name,
                                                       "control_ids": control_ids,
                                                       "workspace_ids": [client.workspace_id]})
        done(f"framework '{cfg.framework_name}', applied to this workspace", True)
        return
    detail = client.get(f"/controlFrameworks/{framework['id']}")   # it may come from another workspace
    current = {c["id"] for c in detail.get("controls") or []}
    if set(control_ids) - current:
        client.patch(f"/controlFrameworks/{framework['id']}", json={"controlIds": sorted(current | set(control_ids))})
    if client.workspace_id not in {w["id"] if isinstance(w, dict) else w for w in detail.get("workspaces") or []}:
        client.post(f"/controlFrameworks/{framework['id']}/workspaces", json={"workspaceIds": [client.workspace_id]})
    done(f"framework '{cfg.framework_name}', applied to this workspace", False)


def create_use_case(client: DeeployClient, cfg: Config, owner_id: str) -> None:
    uc = cfg.use_case
    found = next((u for u in client.get(f"{client.workspace_path}/useCases") if u["name"] == uc["name"]), None)
    client.use_case_id = (found or client.post(f"{client.workspace_path}/useCases", json={
        "name": uc["name"], "description": uc.get("description") or uc["name"], "ownerIds": [owner_id],
        "riskClassification": uc.get("risk_classification", "unclassified")}))["id"]
    done(f"use case '{uc['name']}'", found is None)


def add_report_to_use_case(client: DeeployClient, template_id: str) -> str:
    """Add the test report to the use case. Returns its id there (the pipeline fills it in under that id)."""
    def on_use_case():
        return next((r for r in client.get(f"{client.use_case_path}/documentationTemplates")
                     if r.get("documentationTemplateId") == template_id), None)
    report = on_use_case()
    if report is None:
        client.post(f"{client.use_case_path}/documentationTemplates", json={"documentationTemplateId": template_id})
        report = on_use_case()
    return report["id"]


def add_evidence_to_controls(client: DeeployClient, cfg: Config, report_id: str) -> None:
    """Each control the tests prove gets an explanation and the test report as evidence."""
    controls = client.use_case_controls()
    if missing := [abbr for abbr in cfg.controls if abbr not in controls]:
        print(f"  ! not on the use case: {', '.join(missing)}. Their framework must be applied to this workspace.")
    for abbr in cfg.controls:
        control = controls.get(abbr)
        if control is None:
            continue
        path = f"{client.use_case_path}/useCaseControls/{control['id']}"
        if any(e.get("useCaseDocumentationTemplateId") == report_id for e in client.get(path).get("evidences") or []):
            continue
        if control["status"] == "completed":   # Deeploy only accepts new evidence on a control that isn't completed
            client.patch(path, json={"status": "inProgress"})
        tests = [test for test, proves in cfg.tests.items() if abbr in proves]
        client.post(f"{path}/evidence", json={
            "name": "Automated tests in the CI/CD pipeline", "evidenceType": "text",
            "source": f"Proven on every pipeline run by: {', '.join(tests)}. The linked test report has one line per "
                      f"test, filled in when it passes. A failing test sets this control to In progress."})
        client.post(f"{path}/evidence", json={"name": cfg.report_title, "evidenceType": "linked_template",
                                              "useCaseDocumentationTemplateId": report_id})
        done(f"evidence on {abbr}", True)


def create_approval_rule(client: DeeployClient) -> None:
    """Without an approval rule, Deeploy doesn't ask for approval of new versions."""
    if not client.get(f"{client.workspace_path}/rules/approval"):
        client.post("/rules/approval", json={"workspaceId": client.workspace_id, "owner": 1, "operator": 0,
                                             "reviewer": 0, "applyToNewDeployments": True,
                                             "applyToDeploymentUpdates": True})
        done("approval rule: 1 owner of the workspace approves", True)


def create_deployment(client: DeeployClient, cfg: Config) -> None:
    """A registration deployment: it holds the versions. Deeploy runs nothing and needs no access to your code."""
    name = f"{cfg.use_case['name']} (releases)"
    found = next((d for d in client.get(f"{client.workspace_path}/deployments") if d["name"] == name), None)
    if found:
        client.deployment_id = found["id"]
    else:
        client.deployment_id = client.python_client.create_registration_deployment({
            "name": name, "use_case_id": client.use_case_id,
            "approver_user_ids": client.approver_ids(cfg.approvers),
            "message_to_approvers": "Created by deeploy-ci setup",
            "model_card": f"# {cfg.use_case['name']}\n\nThe CI/CD pipeline registers a version here for every release."}).id
        time.sleep(2)
        for version in client.get(f"{client.deployment_path}/versionHistory"):   # the first version needs no approval
            if version.get("status") == WAITING_FOR_APPROVAL:
                client.patch(f"{client.deployment_path}/versions/{version['id']}/approvalDecision",
                             json={"approvalStatus": "changesRequested", "comment": "Created by setup, not a release"})
    done(f"deployment '{name}'", found is None)


def move_to_production(client: DeeployClient) -> bool:
    """Controls apply from their lifecycle stage on. Ours are production controls, so the use case goes there."""
    current = (client.get(client.use_case_path).get("currentStage") or "exploration").lower()
    if current != "production":
        try:
            for stage in LIFECYCLE[LIFECYCLE.index(current) + 1:]:
                client.post(f"{client.use_case_path}/stage", json={"targetStage": stage})
        except (DeeployError, ValueError) as e:
            print(f"  ! could not move the use case to lifecycle stage Production ({e}).\n"
                  f"    Open it in Deeploy and move its lifecycle stage step by step to Production: {client.use_case_url}")
            return False
    done("lifecycle stage Production (the controls apply from this stage on)", current != "production")
    return True


def print_overview(client: DeeployClient, cfg: Config, report_id: str, in_production: bool) -> None:
    production = ", in lifecycle stage Production" if in_production else " (move it to Production yourself, see above)"
    print(f"""
Done. In Deeploy you now have:
  - the use case '{cfg.use_case['name']}'{production}
      {client.use_case_url}
  - on it, the controls {', '.join(cfg.controls)}, each with the automated check and the test report as evidence
  - the test report, empty until the first pipeline run
      {client.report_url(report_id)}
  - the deployment '{cfg.use_case['name']} (releases)', where the pipeline registers a version for every release
      {client.deployment_url}

The ids are saved in {cfg.path}. Next: commit it (README, step 3).""")
