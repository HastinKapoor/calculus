#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


MISMATCH_RE = re.compile(r"^MISMATCH\s+(.+?):\s+comment=(.+?),\s+expected=(Allowed|Forbidden),\s+actual=(Allowed|Forbidden)\s*$")
CALCULUS_RE = re.compile(r":\s*(Allowed|Forbidden)\s*$")
HERD_OBSERVATION_RE = re.compile(r"^Observation\s+.+?\s+(Never|Sometimes|Always|Maybe)\b", re.MULTILINE)


def parse_failure_paths(report_path: Path) -> list[Path]:
    paths: list[Path] = []
    for line in report_path.read_text(encoding="utf-8").splitlines():
        match = MISMATCH_RE.match(line.strip())
        if match:
            paths.append(Path(match.group(1)).resolve())
    return paths


def run_calculus(runner: str, litmus_path: Path, timeout: int) -> tuple[str | None, str]:
    completed = subprocess.run(
        [sys.executable, runner, str(litmus_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return None, (completed.stderr or "").strip() or output or f"runner exited with {completed.returncode}"
    match = CALCULUS_RE.search(output)
    if not match:
        return None, output or "no Allowed/Forbidden result"
    return match.group(1), output


def run_herd7(memory_model_dir: Path, config_path: Path, litmus_path: Path, timeout: int) -> tuple[str | None, str]:
    completed = subprocess.run(
        ["herd7", "-conf", str(config_path), str(litmus_path)],
        cwd=str(memory_model_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return None, (completed.stderr or "").strip() or output or f"herd7 exited with {completed.returncode}"
    match = HERD_OBSERVATION_RE.search(output)
    if not match:
        return None, output or "no Observation line"
    observation = match.group(1)
    mapped = "Forbidden" if observation == "Never" else "Allowed"
    return mapped, observation


def compare_one(
    litmus_path: Path,
    runner: str,
    memory_model_dir: Path,
    config_path: Path,
    timeout: int,
) -> tuple[str, Path, str | None, str, str | None, str]:
    try:
        calculus_result, calculus_detail = run_calculus(runner, litmus_path, timeout)
    except subprocess.TimeoutExpired:
        return "calculus_timeout", litmus_path, None, f"timeout>{timeout}s", None, ""

    try:
        herd_result, herd_detail = run_herd7(memory_model_dir, config_path, litmus_path, timeout)
    except subprocess.TimeoutExpired:
        return "herd_timeout", litmus_path, calculus_result, calculus_detail, None, f"timeout>{timeout}s"

    if calculus_result is None:
        return "calculus_failure", litmus_path, None, calculus_detail, herd_result, herd_detail
    if herd_result is None:
        return "herd_failure", litmus_path, calculus_result, calculus_detail, None, herd_detail

    if calculus_result == herd_result:
        return "agree", litmus_path, calculus_result, calculus_detail, herd_result, herd_detail
    return "disagree", litmus_path, calculus_result, calculus_detail, herd_result, herd_detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare calculus_heap_test and herd7 on failing Paul McKenney tests.")
    parser.add_argument(
        "--report",
        default="reports/paulmckrcu_report.txt",
        help="Existing mismatch report to read failing source litmus paths from.",
    )
    parser.add_argument(
        "--runner",
        default="calculus_heap_test.py",
        help="Calculus runner to compare against herd7.",
    )
    parser.add_argument(
        "--memory-model-dir",
        default="/home/kapoorh/linux/tools/memory-model",
        help="Directory from which herd7 should be run.",
    )
    parser.add_argument(
        "--config",
        default="./linux-kernel.cfg",
        help="herd7 config path, relative to --memory-model-dir unless absolute.",
    )
    parser.add_argument(
        "--output",
        default="reports/paulmckrcu_vs_herd7.txt",
        help="Output report path.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="Parallel comparisons.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Per-tool timeout in seconds.",
    )
    args = parser.parse_args()

    report_path = Path(args.report)
    memory_model_dir = Path(args.memory_model_dir)
    config_path = Path(args.config)
    output_path = Path(args.output)

    failing_paths = parse_failure_paths(report_path)

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
        for result in executor.map(
            lambda path: compare_one(
                path,
                args.runner,
                memory_model_dir,
                config_path,
                args.timeout,
            ),
            failing_paths,
        ):
            results.append(result)

    counts: dict[str, int] = {}
    for status, *_ in results:
        counts[status] = counts.get(status, 0) + 1

    lines = [
        f"Input failing tests: {len(failing_paths)}",
        f"Agree: {counts.get('agree', 0)}",
        f"Disagree: {counts.get('disagree', 0)}",
        f"Calculus failures: {counts.get('calculus_failure', 0)}",
        f"Calculus timeouts: {counts.get('calculus_timeout', 0)}",
        f"Herd failures: {counts.get('herd_failure', 0)}",
        f"Herd timeouts: {counts.get('herd_timeout', 0)}",
        "",
    ]

    for status, litmus_path, calculus_result, calculus_detail, herd_result, herd_detail in results:
        if status == "agree":
            continue
        lines.append(
            f"{status.upper()} {litmus_path}: "
            f"calculus={calculus_result or calculus_detail}; "
            f"herd7={herd_result or herd_detail}; "
            f"herd7_observation={herd_detail if herd_result is not None else ''}"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output_path)
    for line in lines[:7]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
