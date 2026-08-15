#!/usr/bin/env python3
"""Translate a litmus test into calculus format and run calculus.py."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CALCULUS = REPO_ROOT / "calculus.py"
LINUX_TRANSLATOR = ROOT / "linux_to_calculus.py"
C_TRANSLATOR = ROOT / "c_to_calculus.py"


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)


def prompt_for_language() -> str:
    prompt = (
        "Input language? Enter 'linux' for Linux litmus tests or "
        "'c' for RC11/C litmus tests: "
    )

    while True:
        response = input(prompt).strip().lower()
        if response in {"linux", "c"}:
            return response
        print("Please enter either 'linux' or 'c'.", file=sys.stderr)


def infer_kind(path: Path, requested: str) -> str:
    if requested != "auto":
        return requested

    if path.suffix == ".litmus":
        return prompt_for_language()

    raise SystemExit(
        f"Expected a .litmus input file, got {path.name}. "
        "Use a .litmus test file and specify --kind converted for already translated inputs."
    )


def translate_input(input_path: Path, kind: str, translated_path: Path) -> None:
    if kind == "converted":
        shutil.copyfile(input_path, translated_path)
        return

    translator = C_TRANSLATOR if kind == "c" else LINUX_TRANSLATOR
    result = run_command([sys.executable, str(translator), str(input_path), "-o", str(translated_path)])
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)


def evaluate(translated_path: Path) -> int:
    result = run_command([sys.executable, str(CALCULUS), str(translated_path)])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Translate a Linux or RC11/C .litmus test, then run calculus.py."
    )
    parser.add_argument(
        "input",
        help="Input .litmus file. When --kind is omitted, the script will ask whether it is Linux or RC11/C.",
    )
    parser.add_argument(
        "--kind",
        choices=["auto", "linux", "c", "converted"],
        default="auto",
        help="Interpret the input as Linux litmus, RC11/C litmus, or an already converted file",
    )
    parser.add_argument(
        "--keep-translated",
        help="Optional path where the translated calculus litmus file should be saved",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    kind = infer_kind(input_path, args.kind)
    with tempfile.TemporaryDirectory(prefix="calculus-artifact-") as tmpdir:
        translated_path = Path(tmpdir) / f"{input_path.stem}.translated.litmus"
        translate_input(input_path, kind, translated_path)

        if args.keep_translated:
            keep_path = Path(args.keep_translated).resolve()
            keep_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(translated_path, keep_path)
            print(f"Saved translated litmus to {keep_path}")

        return evaluate(translated_path)


if __name__ == "__main__":
    raise SystemExit(main())
