#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


RESULT_RE = re.compile(r"^\s*\*\s*Result:\s*(.+?)\s*$", re.MULTILINE)
RUNNER_RESULT_RE = re.compile(r":\s*(Allowed|Forbidden)\s*$")


def extract_expected_result(litmus_path: Path) -> tuple[str | None, str | None]:
    text = litmus_path.read_text(encoding="utf-8")
    match = RESULT_RE.search(text)
    if not match:
        return None, None

    annotation = match.group(1).strip()
    normalized = annotation.lower()
    if normalized == "never":
        return annotation, "Forbidden"
    if normalized in {"maybe", "sometimes"}:
        return annotation, "Allowed"
    return annotation, None


def run_test(runner: Path, litmus_path: Path) -> tuple[str | None, str]:
    completed = subprocess.run(
        [sys.executable, str(runner), str(litmus_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        return None, stderr or output or f"runner exited with {completed.returncode}"

    match = RUNNER_RESULT_RE.search(output)
    if not match:
        return None, output or "runner did not print Allowed/Forbidden"

    return match.group(1), output


def evaluate_test(runner: Path, litmus_path: Path) -> tuple[Path, str | None, str | None, str | None, str | None]:
    annotation, expected = extract_expected_result(litmus_path)
    if expected is None:
        return litmus_path, annotation, None, None, None

    actual, detail = run_test(runner, litmus_path)
    return litmus_path, annotation, expected, actual, detail


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Paul McKenney RCU litmus tests and compare with header expectations."
    )
    parser.add_argument(
        "--dir",
        default="litmus/paulmckrcu litmus master auto",
        help="Directory containing .litmus files.",
    )
    parser.add_argument(
        "--runner",
        default="calculus_heap_test.py",
        help="Runner script used for each litmus test.",
    )
    parser.add_argument(
        "--show-matches",
        action="store_true",
        help="Print matching tests as well as mismatches.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 1) // 2),
        help="Number of tests to run in parallel.",
    )
    args = parser.parse_args()

    test_dir = Path(args.dir)
    runner = Path(args.runner)
    litmus_files = sorted(test_dir.rglob("*.litmus"))

    if not litmus_files:
        print(f"No .litmus files found under {test_dir}")
        return 1

    matches = []
    mismatches = []
    unknown_expectations = []
    runner_failures = []

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
        for litmus_path, annotation, expected, actual, detail in executor.map(
            lambda path: evaluate_test(runner, path), litmus_files
        ):
            if expected is None:
                unknown_expectations.append((litmus_path, annotation))
                continue

            if actual is None:
                runner_failures.append((litmus_path, annotation, detail))
                continue

            record = (litmus_path, annotation, expected, actual)
            if actual == expected:
                matches.append(record)
            else:
                mismatches.append(record)

    if args.show_matches:
        for litmus_path, annotation, expected, actual in matches:
            print(
                f"MATCH {litmus_path}: comment={annotation}, expected={expected}, actual={actual}"
            )

    for litmus_path, annotation, expected, actual in mismatches:
        print(
            f"MISMATCH {litmus_path}: comment={annotation}, expected={expected}, actual={actual}"
        )

    for litmus_path, annotation in unknown_expectations:
        if annotation is None:
            print(f"NO_EXPECTATION {litmus_path}: missing Result annotation")
        else:
            print(f"UNKNOWN_EXPECTATION {litmus_path}: comment={annotation}")

    for litmus_path, annotation, detail in runner_failures:
        print(f"RUNNER_FAILURE {litmus_path}: comment={annotation}, detail={detail}")

    print()
    print(f"Total tests: {len(litmus_files)}")
    print(f"Matches: {len(matches)}")
    print(f"Mismatches: {len(mismatches)}")
    print(f"Unknown expectations: {len(unknown_expectations)}")
    print(f"Runner failures: {len(runner_failures)}")

    return 0 if not mismatches and not unknown_expectations and not runner_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
