#!/usr/bin/env python3
"""Compare herd7 outcomes against the calculus pipeline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
LITMUS_ROOT = REPO_ROOT / "litmus"
EVALUATE_PIPELINE = ROOT / "evaluate_calculus_pipeline.py"
HERD_MODELS = REPO_ROOT / "herd7_models"

C_RC11_MODEL = HERD_MODELS / "rc11.cat"
LINUX_CFG = HERD_MODELS / "linux-kernel.cfg"

SUPPORTED_DIRS = ("c", "Kernel", "paulmckrcu")
VALID_RESULTS = {"Allowed", "Forbidden"}


class ComparisonError(RuntimeError):
    pass


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, cwd=cwd)


def infer_source_kind(path: Path) -> str:
    parts = path.resolve().parts
    if "litmus" in parts:
        litmus_index = parts.index("litmus")
        if litmus_index + 1 < len(parts):
            family = parts[litmus_index + 1]
            if family == "c":
                return "c"
            if family in {"Kernel", "paulmckrcu"}:
                return "linux"
    if "converted" in parts:
        converted_index = parts.index("converted")
        if converted_index + 1 < len(parts):
            family = parts[converted_index + 1]
            if family == "c":
                return "c"
            if family in {"Kernel", "paulmckrcu"}:
                return "linux"
    raise ComparisonError(f"Could not infer litmus family from path: {path}")


def to_source_litmus(path: Path) -> Path:
    resolved = path.resolve()
    parts = resolved.parts

    if "litmus" in parts:
        return resolved

    if "converted" not in parts:
        raise ComparisonError(f"Expected a path under litmus/ or converted/, got: {path}")

    converted_index = parts.index("converted")
    family = parts[converted_index + 1]
    relative_parts = list(parts[converted_index + 2 :])
    if not relative_parts:
        raise ComparisonError(f"Converted path is missing a file name: {path}")

    filename = relative_parts[-1]
    if family == "paulmckrcu" and filename.endswith("_converted.litmus"):
        filename = filename.removesuffix("_converted.litmus") + ".litmus"
    relative_parts[-1] = filename

    source_path = LITMUS_ROOT / family / Path(*relative_parts)
    if not source_path.exists():
        raise ComparisonError(f"Could not find matching source litmus for {path}: {source_path}")
    return source_path


def parse_calculus_result(output: str) -> str:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if ": " in stripped:
            candidate = stripped.rsplit(": ", 1)[-1]
            if candidate in VALID_RESULTS:
                return candidate
    raise ComparisonError(f"Could not parse calculus result from output:\n{output}")


def parse_herd_result(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Observation "):
            continue
        tokens = stripped.split()
        if len(tokens) < 3:
            continue
        observation = tokens[2]
        if observation == "Never":
            return "Forbidden"
        if observation in {"Sometimes", "Always"}:
            return "Allowed"

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Test "):
            tokens = stripped.split()
            if tokens and tokens[-1] in VALID_RESULTS:
                return tokens[-1]

    raise ComparisonError(f"Could not parse herd7 result from output:\n{output}")


def run_calculus(source_litmus: Path, kind: str) -> str:
    result = run_command(
        [sys.executable, str(EVALUATE_PIPELINE), str(source_litmus), "--kind", kind],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise ComparisonError(
            f"calculus pipeline failed for {source_litmus}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return parse_calculus_result(result.stdout)


def run_herd_c(source_litmus: Path) -> str:
    if not C_RC11_MODEL.exists():
        raise ComparisonError(f"Missing herd7 RC11 model: {C_RC11_MODEL}")

    result = run_command(
        ["herd7", "-model", str(C_RC11_MODEL), str(source_litmus)],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ComparisonError(
            f"herd7 failed for {source_litmus}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return parse_herd_result(result.stdout)


def run_herd_linux(source_litmus: Path) -> str:
    if not LINUX_CFG.exists():
        raise ComparisonError(f"Missing herd7 Linux model config: {LINUX_CFG}")

    result = run_command(
        ["herd7", "-conf", "linux-kernel.cfg", str(source_litmus)],
        cwd=HERD_MODELS,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ComparisonError(
            f"herd7 failed for {source_litmus}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return parse_herd_result(result.stdout)


def compare_file(path: Path) -> tuple[bool, str, str]:
    source_litmus = to_source_litmus(path)
    kind = infer_source_kind(source_litmus)
    herd_result = run_herd_c(source_litmus) if kind == "c" else run_herd_linux(source_litmus)
    calculus_result = run_calculus(source_litmus, kind)
    return herd_result == calculus_result, herd_result, calculus_result


def gather_all_tests() -> list[Path]:
    tests: list[Path] = []
    for family in SUPPORTED_DIRS:
        tests.extend(sorted((LITMUS_ROOT / family).rglob("*.litmus")))
    return tests


def format_status(path: Path, matches: bool) -> str:
    label = path.resolve().relative_to(REPO_ROOT.resolve())
    return f"{label}: {'MATCH' if matches else 'MISMATCH'}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare herd7 and calculus outcomes for the artifact litmus suites."
    )
    parser.add_argument("input", nargs="?", help="Single litmus file under litmus/ or converted/")
    parser.add_argument("--all", action="store_true", help="Run all litmus tests under c, Kernel, and paulmckrcu")
    args = parser.parse_args()

    if args.all == bool(args.input):
        parser.error("Pass exactly one of a single input file or --all.")

    paths = gather_all_tests() if args.all else [Path(args.input).resolve()]
    failures = 0

    for path in paths:
        try:
            matches, _, _ = compare_file(path)
        except ComparisonError as error:
            print(format_status(path, False))
            print(error, file=sys.stderr)
            failures += 1
            continue

        print(format_status(path, matches))
        if not matches:
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
