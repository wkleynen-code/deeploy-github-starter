# Deeploy as a governance layer on top of GitHub Actions

Your pipeline stays in GitHub Actions. Deeploy adds governance on top of it:

- **Evidence:** after every commit, the test results become evidence in Deeploy, on the controls the tests prove.
- **Approval:** a commit is only deployed after someone approves it in Deeploy.
- **Control changes:** when someone changes a control in Deeploy, GitHub gets an issue for the engineers.

This repository is a small example you can run end to end: a stand-in AI assistant (`example/`) with three tests, a pipeline, and four scenarios that show each part.

## Deeploy in one minute

| In Deeploy | What it is | In this example |
|---|---|---|
| **Use case** | One AI system you govern. Its page holds everything below. | *Example AI assistant* |
| **Control** | A rule the AI system must meet. Its status is *Completed* or *In progress*. | POL-1: every answer has a medical disclaimer. POL-2: no personal data in logs, uncertain answers go to a human. |
| **Framework** | A set of controls, such as the EU AI Act or your own policies. | *Engineering AI policies (starter)*, holding POL-1 and POL-2 |
| **Evidence** | What proves a control is met, shown on the control. | The test report below |
| **Test report** | A form on the use case with one line per test, filled in by the pipeline when the test passed. | 3 lines, one per test |
| **Automated check** | A check on a control that Deeploy marks as met only when the test report is complete. | On POL-1 and POL-2 |
| **Version** | One release of the AI system, waiting for approval or live. | One per commit on `main` |

The pipeline links to the right page in Deeploy wherever it matters, so you don't have to search.

## What you need

- A Deeploy workspace to test in, in which you are an owner.
- Your own copy of this repository on GitHub: on its page, *Use this template* → *Create a new repository*.
- Python 3.9 or newer.

## Step 1: Set up Deeploy (once, on your machine)

Clone your copy and install `deeploy-ci`, the Python code in this repository that talks to Deeploy (the pipeline uses it too):

```bash
git clone https://github.com/<you>/<your-copy>.git
cd <your-copy>
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp .env.example .env
```

In Deeploy, create a personal key (Credentials → Account credentials → + Add) and paste it into `.env`. The key stays on your machine and never goes to GitHub. Then:

```bash
deeploy-ci setup
```

Setup lists your workspaces. Put the id of your test workspace in `deeploy.yaml` as `workspace_id` (at the bottom) and run `deeploy-ci setup` again. It creates the use case, the controls with their automated check, and the empty test report, and prints a link to the use case. Commit `deeploy.yaml` (it now holds the ids), but don't push it yet: the pipeline needs the access from step 2 first. (The run GitHub started when you created your copy stops at `release`: nothing is released before this setup.)

```bash
git add deeploy.yaml
git commit -m "Connect to Deeploy"
```

## Step 2: Give the pipeline access to Deeploy

The pipeline needs a Deeploy credential, stored as a secret in GitHub. Deeploy never gets access to GitHub.

- **In Deeploy:** Credentials → Scoped OAuth credentials → + Add. Give it an end date and these scopes: Organization *read*, Workspace *read*, Use case *read, write, delete*, Deployment *read, write*.
- **In GitHub:** Settings → Environments → New environment, named `deeploy`. Under *Deployment branches*, allow only `main`. Add the credential as the environment secrets `CLIENT_ID` and `CLIENT_SECRET`.

Only the pipeline's Deeploy jobs on `main` can read these secrets.

## Step 3: Play the scenarios

Run them in order. A, B and C are pushes to `main`: each runs the pipeline (Actions → *Pipeline*: jobs `test`, `evidence`, `release`), and what happens depends on what you changed. D uses a second workflow.

To see the Deeploy side, click a control in the run summary of the `evidence` job, or open the use case (setup printed the link) and click a control, e.g. POL-1.

### A. Everything passes

**Do:** `git push` (the commit from step 1).

- **GitHub:** all tests pass. The `evidence` job's summary lists each test and links to POL-1 and POL-2 in Deeploy. The `release` job then waits: its log has a link *Approve it in Deeploy*.
- **Deeploy:** POL-1 and POL-2 are *Completed*. On a control, the evidence shows the test report with 3/3 lines filled in (each with the run, time and commit), and the automated check is met. Open the approval link: a new version with the test results; approve it.
- **GitHub:** the `release` job continues and deploys. In Deeploy the version is now live.

### B. A commit breaks a control

**Do:** in `example/assistant.py` set `DISCLAIMER = ""`, commit and push.

- **GitHub:** the disclaimer test fails, so the `release` job doesn't run: nothing is deployed. Under *Issues* there's a new issue for POL-1 with the failing test and links.
- **Deeploy:** POL-1 is *In progress*. The test report shows 2/3: the line of the failed test is empty. The automated check is no longer met.

### C. The fix

**Do:** put the disclaimer back, commit and push.

- **GitHub:** all tests pass, the issue closes itself, and the `release` job waits for approval again.
- **Deeploy:** POL-1 is *Completed* again, the report is 3/3 and the check is met. Approve the new version to deploy it.

### D. Someone changes a control in Deeploy

This is what happens when, for example, your AI lead makes a rule stricter.

**Do:** change the description of POL-1 in Deeploy, or from your machine: `deeploy-ci change-control POL-1 "Every answer AND every e-mail contains the disclaimer."`. Then in GitHub: Actions → *Deeploy control changes → GitHub issues* → Run workflow. (It also runs once a day by itself.)

- **GitHub:** a new issue with the new text of POL-1, for the engineers to implement and test.
- **Deeploy:** POL-1 is *In progress*. Its evidence says why (the control changed, by whom and when) and links to the GitHub issue. It stays *In progress*, even when the tests pass, until the issue is closed and someone sets POL-1 to *Completed* in Deeploy.

## Settings

Everything is configured in `deeploy.yaml`. The settings you are most likely to change:

| Setting | What it does |
|---|---|
| `tests` | Each test, and the controls it proves. |
| `own_framework` | Defines your own controls (here POL-1 and POL-2: id and description). Setup creates them in Deeploy, in a framework with this name, and puts the automated check on them. Controls that already exist in Deeploy, such as `AIA 50.1` from the EU AI Act, can be used in `tests` directly. |
| `approval.mode` | `hard` (default): the release waits for approval, as in scenario A. `soft`: the version is registered in Deeploy for review, but the release doesn't wait. `off`: no approval step. |
| `approval.approvers` | Who approves, as e-mail addresses of members of the Deeploy workspace, with a role its approval rule accepts (setup creates the rule "1 owner" if there is none). Empty means the owners of the workspace. An address that isn't in the workspace stops the release with an error. |
