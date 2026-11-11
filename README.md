# Deeploy as a governance layer on top of GitHub Actions

Nothing moves to Deeploy. Your code, tests, pipeline and deploys stay in GitHub. Deeploy is a layer on top that your pipeline reports to:

- **Evidence:** after every commit, the pipeline writes the test results into Deeploy, as evidence on the controls the tests prove.
- **Approval:** before deploying, the pipeline registers the commit in Deeploy and waits until someone approves it there.
- **Control changes:** when someone changes a control in Deeploy, the pipeline opens a GitHub issue for the engineers.

This repository is a small example you can run end to end: a stand-in AI assistant (`example/`) with three tests, a pipeline, and four scenarios that show each part.

## Deeploy in two minutes

| In Deeploy | What it is | In this example |
|---|---|---|
| **Workspace** | Where a team's AI systems live. | Your test workspace |
| **Use case** | One AI system you govern. Its page shows its controls and evidence. | *Example AI assistant* |
| **Control** | A rule the AI system must meet. Its status is *Not started*, *In progress* or *Completed*. | POL-1: every answer has a medical disclaimer. POL-2: no personal data in logs, uncertain answers go to a human. |
| **Framework** | A set of controls, such as the EU AI Act or your own policies. | *Engineering AI policies (starter)*, holding POL-1 and POL-2 |
| **Lifecycle stage** | Where the use case is: Exploration, Development, Validation, Production. A control only applies from its stage on. | Production (POL-1 and POL-2 are production controls) |
| **Evidence** | What proves a control is met, shown on the control. | The test report |
| **Test report** | A form on the use case with one line per test, filled in by the pipeline when the test passed. | 3 lines, one per test |
| **Automated check** | A check on a control that Deeploy marks as met only when the test report is complete. | On POL-1 and POL-2 |
| **Deployment** | The place in Deeploy that holds the versions of the AI system. Deeploy doesn't run anything here: your pipeline deploys. | *Example AI assistant (releases)* |
| **Version** | One release. It waits for approval, is deployed, and is then shown as live. | One per commit on `main` |

Every message of the pipeline links to the right page in Deeploy, so you don't have to search.

## What you need

- A Deeploy workspace to test in, in which you are an owner.
- Your own copy of this repository on GitHub: on its page, *Use this template* → *Create a new repository*.
- Python 3.9 or newer.

## Step 1: Get your copy

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

`deeploy-ci` is now a command in this terminal. Run it as `deeploy-ci …`, from this folder and with the virtual environment active (`source .venv/bin/activate` in a new terminal).

## Step 2: Credentials

Two credentials, each in its own place:

- **For you, on your machine:** in Deeploy, Credentials → Account credentials → + Add. Paste the key into `.env`. It stays on your machine and never goes to GitHub.
- **For the pipeline, in GitHub:** in Deeploy, Credentials → Scoped OAuth credentials → + Add. Give it an end date and these scopes: Organization *read*, Workspace *read*, Use case *read, write, delete*, Deployment *read, write*. Then in GitHub: Settings → Environments → New environment, named `deeploy`. Under *Deployment branches*, allow only `main`. Add the credential as the environment secrets `CLIENT_ID` and `CLIENT_SECRET`. Deeploy never gets access to GitHub.

## Step 3: Set up Deeploy

Run setup twice. The first time it only lists your workspaces:

```bash
deeploy-ci setup
```

Put the id of your test workspace in `deeploy.yaml` as `workspace_id` (at the bottom). Then run setup again: **this second run creates everything in Deeploy.**

```bash
deeploy-ci setup
```

What you now have in Deeploy (setup prints the links):

- the use case *Example AI assistant*, in lifecycle stage **Production**. This matters: POL-1 and POL-2 are production controls, and Deeploy only applies them to a use case in that stage. Setup sets it for you; if it can't, it says so: then open the use case in Deeploy and move its lifecycle stage step by step to Production.
- on the use case, the controls POL-1 and POL-2, each with the automated check (not met yet) and the test report as evidence. If other frameworks are applied to your workspace (such as ISO/IEC 42001), their controls show up on the use case too; the example only uses POL-1 and POL-2.
- the test report, still empty
- the deployment *Example AI assistant (releases)*, still without versions

Setup wrote the ids into `deeploy.yaml`. Commit it:

```bash
git add deeploy.yaml
git commit -m "Connect to Deeploy"
```

The run GitHub started when you created your copy stopped at job 3: nothing is released before this setup.

## Step 4: Play the scenarios

Run them in order. A, B and C are pushes to `main`. Each runs the pipeline (Actions → *Pipeline*), with four jobs:

1. **Tests**: runs the tests of the AI system.
2. **Evidence in Deeploy**: writes the results into the test report and updates the controls. Also runs when tests failed.
3. **Approval in Deeploy**: registers the commit as a version in Deeploy and waits until it's approved. Only when every test passed.
4. **Deploy**: deploys, and marks the version live in Deeploy.

The summary of each job (on the run's page) says what happened in Deeploy and links to the pages. Scenario D uses a second workflow.

### A. Everything passes

**Do:** `git push`

- **Jobs 1 and 2:** all tests pass. In Deeploy the test report is filled in (3/3, each line with the run and commit), the automated check on POL-1 and POL-2 is met, and both are *Completed*.
- **Job 3** waits for approval. Its log has the link to the version in Deeploy. There, click **Approve**, then **Deploy**. (If you only approve, job 4 deploys it for you.)
- **Job 4:** deploys. In Deeploy the version is now live on the deployment.

### B. A commit breaks a control

**Do:** in `example/assistant.py` set `DISCLAIMER = ""`, commit and push.

- **Job 1:** the disclaimer test fails. Jobs 3 and 4 don't run: nothing is registered or deployed.
- **Job 2:** in Deeploy POL-1 is *In progress*, the test report shows 2/3 (the failed test's line is empty), and the automated check is no longer met. In GitHub there's a new issue for POL-1, with the failing test and links.

### C. The fix

**Do:** put the disclaimer back, commit and push.

- **Job 2:** the report is 3/3 again, the check is met, POL-1 is *Completed*, and the issue closes itself.
- **Jobs 3 and 4:** as in A: approve (and deploy) the new version in Deeploy.

### D. Someone changes a control in Deeploy

This is what happens when, for example, your AI lead makes a control stricter.

**Do:** in Deeploy, go to the organization's control frameworks, open *Engineering AI policies (starter)*, find POL-1 and edit its description (e.g. "Every answer AND every e-mail contains the disclaimer."). Or from your terminal: `deeploy-ci change-control POL-1 "Every answer AND every e-mail contains the disclaimer."`. Then in GitHub: Actions → *Deeploy control changes → GitHub issues* → Run workflow. (It also runs once a day by itself.)

- **GitHub:** a new issue with the new description of POL-1, for the engineers to implement and test.
- **Deeploy:** POL-1 is *In progress*. Its evidence says what changed, by whom and when, and links to the issue. It stays *In progress*, even when the tests pass, until the issue is closed and someone sets POL-1 back to *Completed* in Deeploy.

## Settings

Everything is configured in `deeploy.yaml`. The settings you are most likely to change:

| Setting | What it does |
|---|---|
| `tests` | Each test, and the controls it proves. |
| `own_framework` | Defines your own controls (here POL-1 and POL-2: id and description). Setup creates them in Deeploy, in a framework with this name, and puts the automated check on them. Controls and frameworks are shared by all workspaces of your organization, so setup reuses them when they exist, and a change to one shows up everywhere it's used. Controls that already exist in Deeploy, such as `AIA 50.1` from the EU AI Act, can be used in `tests` directly. |
| `approval.mode` | `hard` (default): job 3 waits for approval, as in scenario A. `soft`: the version is registered in Deeploy for review, but the release doesn't wait. `off`: no approval step. |
| `approval.approvers` | Who approves, as e-mail addresses of members of the Deeploy workspace, with a role its approval rule accepts (setup creates the rule "1 owner" if there is none). Empty means the owners of the workspace. An address that isn't in the workspace stops the release with an error. |
