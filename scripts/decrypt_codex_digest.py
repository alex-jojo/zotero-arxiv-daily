"""Decrypt the private recommendation input downloaded from GitHub Actions."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def find_openssl() -> str:
    executable = shutil.which("openssl")
    if executable:
        return executable
    git_openssl = Path(r"C:\Program Files\Git\usr\bin\openssl.exe")
    if git_openssl.exists():
        return str(git_openssl)
    raise FileNotFoundError("OpenSSL was not found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("encrypted", type=Path)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            find_openssl(),
            "cms",
            "-decrypt",
            "-inform",
            "DER",
            "-in",
            str(args.encrypted),
            "-inkey",
            str(args.key),
            "-out",
            str(args.output),
        ],
        check=True,
    )
    print(f"Decrypted recommendation input to {args.output}")


if __name__ == "__main__":
    main()
