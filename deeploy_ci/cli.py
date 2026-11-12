"""deeploy-ci: the GitHub pipeline's link to Deeploy.

  setup                 once, on your machine: create the example in Deeploy and save the ids in deeploy.yaml
  test-summary FILE     job 1: the test results in the run summary (no Deeploy needed)
  report FILE           job 2: the test results into Deeploy, as evidence on the controls
  gate                  job 3: register the version in Deeploy and wait until it's approved there
  released VERSION_ID   job 4: mark the version live in Deeploy
  watch                 daily workflow: controls changed in Deeploy -> GitHub issues
  change-control ID TEXT  change one of your controls, as your AI lead would in Deeploy
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import control_changes, release_approval, setup_deeploy, test_results
from .config import Config
from .deeploy_client import DeeployError, DeeployUnavailable
from .github import notice, summary

COMMANDS = {"setup": setup_deeploy.setup, "test-summary": test_results.test_summary, "report": test_results.report,
            "gate": release_approval.gate, "released": release_approval.released, "watch": control_changes.watch,
            "change-control": control_changes.change_control}


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="deeploy-ci", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("setup")
    commands.add_parser("test-summary").add_argument("junit", nargs="?", default="results.xml")
    commands.add_parser("report").add_argument("junit", nargs="?", default="results.xml")
    commands.add_parser("gate")
    commands.add_parser("released").add_argument("version_id")
    commands.add_parser("watch")
    change = commands.add_parser("change-control")
    change.add_argument("control")
    change.add_argument("text")
    return parser.parse_args(argv)


def load_dotenv(path: Path = Path(".env")) -> None:
    """On your machine the credentials come from .env; real environment variables win."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    load_dotenv()
    cfg = Config.load()

    # Before setup there is nothing in Deeploy yet. The jobs then do nothing; without an approval job 4 doesn't
    # run, so nothing is deployed.
    if args.command not in ("setup", "test-summary") and not cfg.is_set_up:
        notice("Deeploy is not set up yet, so this job did nothing and nothing is deployed. Follow the README, "
               "step 3 (deeploy-ci setup), then push again.", title="Deeploy not set up yet")
        summary(f"## Deeploy is not set up yet: `{args.command}` skipped\n\nNothing is sent to Deeploy and nothing "
                f"is deployed. Run `deeploy-ci setup`, commit `deeploy.yaml` and push (README, step 3).")
        return

    try:
        COMMANDS[args.command](cfg, args)
    except DeeployUnavailable as e:
        # Evidence and control changes may wait for the next run. Approving and deploying never go on without Deeploy.
        if args.command in ("report", "watch"):
            notice(f"Deeploy did not answer ({e}). Skipped; the next run catches up.", title="Deeploy unreachable",
                   level="warning")
            summary(f"## Deeploy unreachable: `{args.command}` skipped\n\nThe next run brings Deeploy up to date.")
            return
        notice(f"Deeploy did not answer ({e}). Nothing is deployed.", title="Deeploy unreachable", level="error")
        sys.exit(1)
    except DeeployError as e:
        notice(str(e), title="Deeploy refused a request", level="error")
        print(e)
        sys.exit(1)
