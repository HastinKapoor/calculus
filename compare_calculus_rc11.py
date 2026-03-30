#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run calculus_test.py and rc11_test.py on every .litmus file in a folder "
            "and print the files where their results differ."
        )
    )
    parser.add_argument("input_dir", help="Folder containing .litmus files")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search for .litmus files recursively",
    )
    return parser.parse_args()


def collect_litmus_files(input_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.litmus" if recursive else "*.litmus"
    return sorted(path for path in input_dir.glob(pattern) if path.is_file())


def run_checker(script_path: Path, litmus_file: Path) -> tuple[str, str]:
    result = subprocess.run(
        [sys.executable, str(script_path), str(litmus_file)],
        capture_output=True,
        text=True,
    )

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()

    if result.returncode != 0:
        details = error or output or f"exit code {result.returncode}"
        return "ERROR", details

    for line in reversed(output.splitlines()):
        if ": " not in line:
            continue
        _, verdict = line.rsplit(": ", 1)
        verdict = verdict.strip()
        if verdict in {"Allowed", "Forbidden"}:
            return verdict, output

    details = output or error or "could not parse checker output"
    return "ERROR", details


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    input_dir = Path(args.input_dir).expanduser().resolve()

    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 2

    calculus_script = root / "calculus_test.py"
    rc11_script = root / "rc11_test.py"

    missing = [path for path in (calculus_script, rc11_script) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"Required script not found: {path}", file=sys.stderr)
        return 2

    files = collect_litmus_files(input_dir, args.recursive)
    if not files:
        print(f"No .litmus files found in {input_dir}")
        return 0

    mismatches = []
    for litmus_file in files:
        calculus_result, _ = run_checker(calculus_script, litmus_file)
        rc11_result, _ = run_checker(rc11_script, litmus_file)

        if calculus_result != rc11_result:
            mismatches.append((litmus_file, calculus_result, rc11_result))

    if not mismatches:
        print("No differences found.")
        return 0

    for litmus_file, calculus_result, rc11_result in mismatches:
        print(
            f"{litmus_file.name}: calculus_test={calculus_result}, rc11_test={rc11_result}"
        )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
