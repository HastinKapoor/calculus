#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "For each litmus test in converted/Allowed_C11, run rc11_test.py on the "
            "base test and then run calculus_test.py on all corresponding "
            "interchangeability variants."
        )
    )
    root = Path(__file__).resolve().parent
    parser.add_argument(
        "--allowed-dir",
        default=str(root / "converted" / "Allowed_C11"),
        help="Directory containing the base converted Allowed_C11 .litmus files",
    )
    parser.add_argument(
        "--interchangeability-dir",
        default=str(root / "converted" / "Allowed_C11_Interchangeability"),
        help="Directory containing interchangeability subdirectories",
    )
    return parser.parse_args()


def run_command(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, capture_output=True, text=True)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def print_stream(text: str):
    if text:
        print(text)


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    allowed_dir = Path(args.allowed_dir).expanduser().resolve()
    interchangeability_dir = Path(args.interchangeability_dir).expanduser().resolve()
    rc11_script = root / "rc11_test.py"
    calculus_script = root / "calculus_test.py"

    if not allowed_dir.is_dir():
        print(f"Allowed directory not found: {allowed_dir}", file=sys.stderr)
        return 2
    if not interchangeability_dir.is_dir():
        print(
            f"Interchangeability directory not found: {interchangeability_dir}",
            file=sys.stderr,
        )
        return 2
    if not rc11_script.is_file():
        print(f"Required script not found: {rc11_script}", file=sys.stderr)
        return 2
    if not calculus_script.is_file():
        print(f"Required script not found: {calculus_script}", file=sys.stderr)
        return 2

    litmus_files = sorted(allowed_dir.glob("*.litmus"))
    if not litmus_files:
        print(f"No .litmus files found in {allowed_dir}")
        return 0

    failures = []

    for litmus_file in litmus_files:
        test_name = litmus_file.stem
        variant_dir = interchangeability_dir / test_name

        print(f"=== Base RC11: {litmus_file} ===")
        returncode, stdout, stderr = run_command(
            [sys.executable, str(rc11_script), str(litmus_file)]
        )
        print_stream(stdout)
        print_stream(stderr)
        if returncode != 0:
            failures.append(f"rc11_test failed for {litmus_file}")
            continue

        if not variant_dir.is_dir():
            message = f"Missing interchangeability directory: {variant_dir}"
            print(message, file=sys.stderr)
            failures.append(message)
            continue

        variant_files = sorted(variant_dir.glob("*.litmus"))
        if not variant_files:
            message = f"No interchangeability .litmus files found in {variant_dir}"
            print(message, file=sys.stderr)
            failures.append(message)
            continue

        print(f"=== Interchangeability: {variant_dir} ===")
        for variant_file in variant_files:
            returncode, stdout, stderr = run_command(
                [sys.executable, str(calculus_script), str(variant_file)]
            )
            print_stream(stdout)
            print_stream(stderr)
            if returncode != 0:
                failures.append(f"calculus_test failed for {variant_file}")

    if failures:
        print("\nCompleted with failures:", file=sys.stderr)
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print("\nCompleted successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
