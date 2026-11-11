"""deeploy-ci: connect a GitHub Actions pipeline to Deeploy.

  setup                 once: create the example in Deeploy and save the ids in deeploy.yaml
  report RESULTS.xml    after the tests: results -> evidence on the controls
  gate                  before the deploy: register the version, wait for approval
  released VERSION_ID   after the deploy: mark the version live
  watch                 control changes in Deeploy -> GitHub issues
  change-control ID TEXT  change one of your controls, as your AI lead would in Deeploy
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import approval, changes, evidence, provision
from .config import Config
from .deeploy import DeeployError, DeeployUnavailable
from .github import summary

COMMANDS = {"setup": provision.setup, "report": evidence.report, "gate": approval.gate,
            "released": approval.released, "watch": changes.watch, "change-control": changes.change_control}


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="deeploy-ci", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("setup", help="create the example in Deeploy")
    r = sub.add_parser("report", help="test results -> evidence")
    r.add_argument("junit", nargs="?", default="results.xml", help="JUnit XML file (default: results.xml)")
    sub.add_parser("gate", help="register the version and wait for approval")
    sub.add_parser("released", help="mark a version live").add_argument("version_id")
    w = sub.add_parser("watch", help="control changes -> GitHub issues")
    w.add_argument("--minutes", type=int, default=25 * 60, help="how far back to look (default: 25 hours)")
    c = sub.add_parser("change-control", help="change one of your controls")
    c.add_argument("control")
    c.add_argument("text")
    return p.parse_args(argv)


def load_dotenv(path: Path = Path(".env")) -> None:
    """Local runs read credentials from .env; real environment variables win."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    load_dotenv()
    cfg = Config.load()
    if args.command != "setup" and not all(cfg.deeploy.get(k) for k in ("workspace_id", "use_case_id", "deployment_id")):
        blocked = args.command in ("gate", "released")   # never release without an approval
        summary(f"## Deeploy is not set up yet: `{args.command}` {'blocked, nothing is released' if blocked else 'skipped'}"
                f"\n\nRun `deeploy-ci setup` and commit `deeploy.yaml` (README, step 1).")
        sys.exit(1 if blocked else 0)
    try:
        COMMANDS[args.command](cfg, args)
    except DeeployUnavailable as e:
        if cfg.on_deeploy_down(args.command) == "skip" and args.command in ("report", "gate", "released", "watch"):
            print(f"::warning::Deeploy did not answer ({e}); skipped, the next run catches up.")
            summary(f"## Deeploy unreachable: `{args.command}` skipped\n\nThe pipeline continues; the next run "
                    f"brings Deeploy up to date.")
            return
        print(f"::error::Deeploy did not answer ({e}).")
        sys.exit(1)
    except DeeployError as e:
        print(f"::error::{e}")
        sys.exit(1)
