"""Dispatch a local Codex-produced digest to the GitHub mail workflow."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
from pathlib import Path


REPOSITORY = "alex-jojo/zotero-arxiv-daily"
WORKFLOW = "send-codex-digest.yml"


def run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("digest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    digest = json.loads(args.digest.read_text(encoding="utf-8"))
    if digest.get("model") != "gpt-5.6-sol" or len(digest.get("papers", [])) != 3:
        raise ValueError("Expected three papers selected by gpt-5.6-sol")

    encoded = base64.b64encode(
        json.dumps(digest, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > 60_000:
        raise ValueError("Digest is too large for a GitHub workflow input")
    if args.dry_run:
        print(f"Digest validated; encoded payload is {len(encoded)} characters")
        return

    before = run(
        "gh", "run", "list", "--repo", REPOSITORY, "--workflow", WORKFLOW,
        "--limit", "1", "--json", "databaseId", "--jq", ".[0].databaseId",
    )
    request = json.dumps(
        {"ref": "main", "inputs": {"digest_b64": encoded}},
        ensure_ascii=True,
    )
    run(
        "gh", "api", f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches",
        "--method", "POST", "--input", "-", input_text=request,
    )

    run_id = ""
    for _ in range(20):
        import time

        time.sleep(3)
        run_id = run(
            "gh", "run", "list", "--repo", REPOSITORY, "--workflow", WORKFLOW,
            "--limit", "1", "--json", "databaseId", "--jq", ".[0].databaseId",
        )
        if run_id and run_id != before:
            break
    if not run_id or run_id == before:
        raise RuntimeError("Could not find the newly dispatched email workflow")

    subprocess.run(
        ["gh", "run", "watch", run_id, "--repo", REPOSITORY, "--exit-status"],
        check=True,
    )
    print(f"Email workflow completed successfully: {run_id}")


if __name__ == "__main__":
    main()
