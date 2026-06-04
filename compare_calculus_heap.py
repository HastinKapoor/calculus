#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run calculus_test.py and calculus_heap_test.py on a litmus input set "
            "and print only the files where their results differ."
        )
    )
    parser.add_argument(
        "input_path",
        help="A .litmus file or a folder containing .litmus files",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search for .litmus files recursively when input_path is a folder",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include full error details instead of printing only ERROR",
    )
    return parser.parse_args()


def collect_litmus_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix == ".litmus" else []

    pattern = "**/*.litmus" if recursive else "*.litmus"
    return sorted(path for path in input_path.glob(pattern) if path.is_file())


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


def format_result(verdict: str, details: str, verbose: bool) -> str:
    if verdict == "ERROR":
        if not verbose:
            return "ERROR"
        single_line = " ".join(details.split())
        return f"ERROR ({single_line})"
    return verdict


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    input_path = Path(args.input_path).expanduser().resolve()

    if not input_path.exists():
        print(f"Input path not found: {input_path}", file=sys.stderr)
        return 2

    calculus_script = root / "calculus_test.py"
    heap_script = root / "calculus_heap_test.py"

    missing = [path for path in (calculus_script, heap_script) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"Required script not found: {path}", file=sys.stderr)
        return 2

    files = collect_litmus_files(input_path, args.recursive)
    if not files:
        print(f"No .litmus files found in {input_path}")
        return 0

    differences = []
    base_dir = input_path if input_path.is_dir() else input_path.parent

    for litmus_file in files:
        calculus_result, calculus_details = run_checker(calculus_script, litmus_file)
        heap_result, heap_details = run_checker(heap_script, litmus_file)

        if calculus_result != heap_result or calculus_details != heap_details and "ERROR" in {calculus_result, heap_result}:
            try:
                display_name = litmus_file.relative_to(base_dir)
            except ValueError:
                display_name = litmus_file
            differences.append(
                (
                    display_name,
                    format_result(calculus_result, calculus_details, args.verbose),
                    format_result(heap_result, heap_details, args.verbose),
                )
            )

    if not differences:
        return 0

    for litmus_file, calculus_result, heap_result in differences:
        print(f"{litmus_file}: calculus_test={calculus_result}, calculus_heap_test={heap_result}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
