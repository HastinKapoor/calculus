#!/usr/bin/env python3
"""Compare herd7 outcomes against the calculus pipeline."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
LITMUS_ROOT = REPO_ROOT / "litmus"
EVALUATE_PIPELINE = ROOT / "evaluate_calculus_pipeline.py"
HERD_MODELS = REPO_ROOT / "herd7_models"
CONVERTED_ROOT = REPO_ROOT / "converted"
LINUX_TRANSLATOR = ROOT / "linux_to_calculus.py"
C_TRANSLATOR = ROOT / "c_to_calculus.py"
TOGGLE_STRENGTH = ROOT / "toggle_memorder_strength.sh"
TOGGLE_LANGUAGE = ROOT / "toggle_memorder_language.sh"
TOGGLE_SOURCE_STRENGTH = ROOT / "toggle_memorder_source_strength.sh"
COMBO_SUFFIX_RE = re.compile(r"^(?P<base>.+)_combo_(?P<bits>[01]+)$")
INTERCHANGE_BASELINE_DIRS = ("c", "Kernel")
MEMORDER_VARIANT_TOKENS = {
    "Racq",
    "Rna",
    "Rrlx",
    "Rsc",
    "Wna",
    "Wrel",
    "Wrlx",
    "Wsc",
    "acq",
    "rel",
    "rlx",
    "sc",
}

C_RC11_MODEL = HERD_MODELS / "rc11.cat"
LINUX_CFG = HERD_MODELS / "linux-kernel.cfg"

SUPPORTED_DIRS = ("c", "Kernel")
VALID_RESULTS = {"Allowed", "Forbidden"}
INTERCHANGE_PROGRESS_FILE = REPO_ROOT / ".interchange_progress.json"
INTERCHANGE_STOP_FILE = REPO_ROOT / ".interchange_stop"


class ComparisonError(RuntimeError):
    pass


class InterchangeStopRequested(RuntimeError):
    pass


def print_variant_summary(total_variants: int, matches: int, mismatches: int) -> None:
    print("Summary:", flush=True)
    print(f"  Total variants tested: {total_variants}", flush=True)
    print(f"  Matches: {matches}", flush=True)
    print(f"  Mismatches: {mismatches}", flush=True)


def print_summary(total: int, failures: list[str]) -> None:
    print("Summary:", flush=True)
    print(f"  Total tests: {total}", flush=True)
    print(f"  Mismatches: {len(failures)}", flush=True)
    if failures:
        print("  Mismatch list:", flush=True)
        for label in failures:
            print(f"    {label}", flush=True)


def write_interchange_progress(
    *,
    total_original_tests: int,
    processed_original_tests: int,
    tested_variants: int,
    match_count: int,
    mismatch_count: int,
    failures: list[str],
) -> None:
    payload = {
        "total_original_tests": total_original_tests,
        "processed_original_tests": processed_original_tests,
        "tested_variants": tested_variants,
        "matches": match_count,
        "mismatches": mismatch_count,
        "failures": failures,
    }
    INTERCHANGE_PROGRESS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def reset_interchange_run_state() -> None:
    INTERCHANGE_PROGRESS_FILE.unlink(missing_ok=True)
    INTERCHANGE_STOP_FILE.unlink(missing_ok=True)


def maybe_stop_interchange() -> None:
    if INTERCHANGE_STOP_FILE.exists():
        raise InterchangeStopRequested(
            f"Stop requested via {INTERCHANGE_STOP_FILE.relative_to(REPO_ROOT)}."
        )


def print_interchange_summary(
    *,
    representative_groups: dict[Path, list[Path]],
    processed_original_tests: int,
    tested_variants: int,
    match_count: int,
    mismatch_count: int,
    stopped_early: bool,
) -> None:
    print(
        "Interchange representatives: "
        f"{len(representative_groups)} groups from "
        f"{sum(len(paths) for paths in representative_groups.values())} inputs.",
        flush=True,
    )
    if stopped_early:
        print(
            f"Stopped early after {processed_original_tests} original interchange tests.",
            flush=True,
        )
        print(
            f"Progress saved to {INTERCHANGE_PROGRESS_FILE.relative_to(REPO_ROOT)}.",
            flush=True,
        )
        print(
            f"Create {INTERCHANGE_STOP_FILE.relative_to(REPO_ROOT)} before the next test to stop gracefully without Ctrl+C.",
            flush=True,
        )
    print_variant_summary(tested_variants, match_count, mismatch_count)


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


def compare_litmus(source_litmus: Path, kind: str) -> tuple[bool, str, str]:
    herd_result = run_herd_c(source_litmus) if kind == "c" else run_herd_linux(source_litmus)
    calculus_result = run_calculus(source_litmus, kind)
    return herd_result == calculus_result, herd_result, calculus_result


def compare_file(path: Path) -> tuple[bool, str, str]:
    source_litmus = to_source_litmus(path)
    kind = infer_source_kind(source_litmus)
    return compare_litmus(source_litmus, kind)


def gather_all_tests() -> list[Path]:
    tests: list[Path] = []
    for family in SUPPORTED_DIRS:
        tests.extend(sorted((LITMUS_ROOT / family).rglob("*.litmus")))
    return tests


def gather_suite_tests(suite: str) -> list[Path]:
    if suite == "c":
        return sorted((LITMUS_ROOT / "c").rglob("*.litmus"))
    if suite == "linux":
        return sorted((LITMUS_ROOT / "Kernel").rglob("*.litmus"))
    if suite == "RCU":
        return sorted((LITMUS_ROOT / "paulmckrcu").rglob("*.litmus"))
    raise ComparisonError(f"Unsupported comparison suite: {suite}")


def require_success(result: subprocess.CompletedProcess[str], description: str) -> None:
    if result.returncode == 0:
        return
    if result.stdout:
        print(result.stdout, end="", file=sys.stderr)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    raise ComparisonError(f"{description} failed with exit code {result.returncode}.")


def evaluate_litmus(path: Path) -> str:
    result = run_command([sys.executable, str(REPO_ROOT / "calculus.py"), str(path)])
    require_success(result, f"calculus evaluation for {path}")
    return parse_calculus_result(result.stdout)


def parse_combo_stem(path: Path) -> tuple[str, str]:
    match = COMBO_SUFFIX_RE.fullmatch(path.stem)
    if not match:
        raise ComparisonError(f"Expected combo suffix in generated file: {path}")
    return match.group("base"), match.group("bits")


def representative_key(path: Path) -> str:
    parts = path.stem.split("+")
    while len(parts) > 1 and parts[-1] in MEMORDER_VARIANT_TOKENS:
        parts.pop()
    return "+".join(parts)


def iter_baseline_inputs(input_dir: Path) -> list[Path]:
    if input_dir.name == "converted":
        litmus_paths: list[Path] = []
        for family in INTERCHANGE_BASELINE_DIRS:
            family_dir = input_dir / family
            if family_dir.is_dir():
                litmus_paths.extend(sorted(family_dir.rglob("*.litmus")))
        return litmus_paths
    return sorted(input_dir.rglob("*.litmus"))


def select_representative_inputs(input_dir: Path) -> dict[Path, list[Path]]:
    groups: dict[Path, list[Path]] = defaultdict(list)
    for litmus_path in iter_baseline_inputs(input_dir):
        rel_path = litmus_path.relative_to(input_dir)
        key = rel_path.with_name(f"{representative_key(rel_path)}.litmus")
        groups[key].append(litmus_path)
    return groups


def stage_representative_inputs(input_dir: Path, staged_dir: Path) -> tuple[Path, dict[Path, list[Path]]]:
    groups = select_representative_inputs(input_dir)
    staged_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, variants in groups.items():
        destination = staged_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        preferred = input_dir / rel_path
        representative = preferred if preferred in variants else variants[0]
        shutil.copy2(representative, destination)
    return staged_dir, groups


def translate_source_file(source_litmus: Path, translated_path: Path, kind: str) -> None:
    translator = C_TRANSLATOR if kind == "c" else LINUX_TRANSLATOR
    translated_path.parent.mkdir(parents=True, exist_ok=True)
    require_success(
        run_command([sys.executable, str(translator), str(source_litmus), "-o", str(translated_path)]),
        f"translation for {source_litmus}",
    )


def run_generated_strength_suite(paths: list[Path]) -> int:
    with tempfile.TemporaryDirectory(prefix="run-artifact-all-") as tmpdir:
        tmp_root = Path(tmpdir)
        failures: list[str] = []
        generated_count = 0
        for source_litmus in sorted(paths):
            kind = infer_source_kind(source_litmus)
            rel_path = source_litmus.resolve().relative_to(LITMUS_ROOT.resolve())
            test_root = tmp_root / "per_test" / rel_path.with_suffix("")
            input_dir = test_root / "input"
            source_variants_dir = test_root / "source_variants"
            translated_variants_dir = test_root / "translated_variants"
            staged_source = input_dir / rel_path.name
            input_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_litmus, staged_source)
            require_success(
                run_command(
                    [
                        str(TOGGLE_SOURCE_STRENGTH),
                        str(input_dir),
                        str(source_variants_dir),
                        "--kind",
                        kind,
                    ]
                ),
                f"toggle_memorder_source_strength for {source_litmus}",
            )

            generated_paths = sorted(source_variants_dir.rglob("*.litmus"))
            if not generated_paths:
                print(
                    f"generated/{rel_path.as_posix()}: SKIPPED (no source strength variants)",
                    flush=True,
                )
                continue

            for generated_source in generated_paths:
                rel_generated = generated_source.relative_to(source_variants_dir)
                translated_path = translated_variants_dir / rel_generated
                generated_count += 1
                try:
                    herd_result = run_herd_c(generated_source) if kind == "c" else run_herd_linux(generated_source)
                    translate_source_file(generated_source, translated_path, kind)
                    calculus_result = evaluate_litmus(translated_path)
                    matches = herd_result == calculus_result
                except ComparisonError as error:
                    label = Path("generated") / rel_path.parent / rel_generated
                    print(f"{label.as_posix()}: MISMATCH", flush=True)
                    print(error, file=sys.stderr, flush=True)
                    failures.append(label.as_posix())
                    continue

                label = Path("generated") / rel_path.parent / rel_generated
                print(f"{label.as_posix()}: {'MATCH' if matches else 'MISMATCH'}", flush=True)
                if not matches:
                    failures.append(label.as_posix())

        print_summary(generated_count, failures)
        return 1 if failures else 0


def run_interchange_suite(stop_after: int | None = None) -> int:
    if not CONVERTED_ROOT.is_dir():
        raise ComparisonError(f"Converted test directory not found: {CONVERTED_ROOT}")

    reset_interchange_run_state()
    with tempfile.TemporaryDirectory(prefix="run-artifact-interchange-") as tmpdir:
        tmp_root = Path(tmpdir)
        _, representative_groups = stage_representative_inputs(
            CONVERTED_ROOT, tmp_root / "representative"
        )
        failures: list[str] = []
        processed_original_tests = 0
        tested_variants = 0
        match_count = 0
        mismatch_count = 0
        write_interchange_progress(
            total_original_tests=len(representative_groups),
            processed_original_tests=processed_original_tests,
            tested_variants=tested_variants,
            match_count=match_count,
            mismatch_count=mismatch_count,
            failures=failures,
        )

        stopped_early = False
        try:
            for group_rel, variants in sorted(representative_groups.items()):
                maybe_stop_interchange()
                if stop_after is not None and processed_original_tests >= stop_after:
                    stopped_early = True
                    break
                representative = variants[0]
                test_root = tmp_root / "per_test" / group_rel.with_suffix("")
                input_dir = test_root / "input"
                strength_dir = test_root / "strength"
                language_dir = test_root / "language"
                input_path = input_dir / group_rel
                input_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(representative, input_path)

                require_success(
                    run_command([str(TOGGLE_STRENGTH), str(input_dir), str(strength_dir)]),
                    f"toggle_memorder_strength for {representative}",
                )
                require_success(
                    run_command([str(TOGGLE_LANGUAGE), str(strength_dir), str(language_dir)]),
                    f"toggle_memorder_language for {representative}",
                )

                generated_groups: dict[Path, list[tuple[str, Path]]] = defaultdict(list)
                for litmus_path in sorted(language_dir.rglob("*.litmus")):
                    rel_path = litmus_path.relative_to(language_dir)
                    group_base, bits = parse_combo_stem(rel_path)
                    generated_group_rel = rel_path.with_name(f"{group_base}.litmus")
                    generated_groups[generated_group_rel].append((bits, litmus_path))

                if not generated_groups:
                    raise ComparisonError(f"No interchange variants generated for {representative}")
                for label, generated_variants in sorted(generated_groups.items()):
                    ordered_variants = sorted(generated_variants, key=lambda item: item[0])
                    baseline_bits = "0" * len(ordered_variants[0][0])
                    if ordered_variants[0][0] != baseline_bits:
                        raise ComparisonError(
                            f"Expected baseline variant {baseline_bits} for {label}, "
                            f"found {ordered_variants[0][0]}"
                        )

                    verdicts = {
                        bits: evaluate_litmus(litmus_path)
                        for bits, litmus_path in ordered_variants[1:]
                    }
                    variant_count = 1 + len(ordered_variants[1:])
                    tested_variants += variant_count
                    observed = set(verdicts.values())
                    label_text = label.as_posix()
                    matches = len(observed) <= 1
                    print(f"interchange/{label_text}: {'MATCH' if matches else 'MISMATCH'}", flush=True)
                    if not matches:
                        failures.append(f"interchange/{label_text}")
                        mismatch_count += variant_count
                        details = ", ".join(
                            f"combo_{bits}={verdict}" for bits, verdict in sorted(verdicts.items())
                        )
                        print(details, file=sys.stderr, flush=True)
                    else:
                        match_count += variant_count
                processed_original_tests += 1
                write_interchange_progress(
                    total_original_tests=len(representative_groups),
                    processed_original_tests=processed_original_tests,
                    tested_variants=tested_variants,
                    match_count=match_count,
                    mismatch_count=mismatch_count,
                    failures=failures,
                )
        except KeyboardInterrupt:
            stopped_early = True
            print("\nInterchange run interrupted by user.", file=sys.stderr, flush=True)
        except InterchangeStopRequested as stop_request:
            stopped_early = True
            print(stop_request, file=sys.stderr, flush=True)

        print_interchange_summary(
            representative_groups=representative_groups,
            processed_original_tests=processed_original_tests,
            tested_variants=tested_variants,
            match_count=match_count,
            mismatch_count=mismatch_count,
            stopped_early=stopped_early,
        )
        return 1 if failures else 0


def format_status(path: Path, matches: bool) -> str:
    resolved = path.resolve()
    try:
        label = resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        label = resolved
    return f"{label}: {'MATCH' if matches else 'MISMATCH'}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare herd7 and calculus outcomes for the artifact litmus suites."
    )
    parser.add_argument("input", nargs="?", help="Single litmus file under litmus/ or converted/")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Translate the c and Kernel suites, generate strength variants, and compare all generated tests",
    )
    parser.add_argument(
        "--kind",
        choices=["c", "linux"],
        help="Required with a single input file; identifies whether the test should use the C or Linux model",
    )
    parser.add_argument(
        "--suite",
        choices=["c", "linux", "RCU", "interchange"],
        help="Run a specific suite: C, Linux/Kernel, RCU, or the generated interchangeability suite",
    )
    parser.add_argument(
        "--stop-after",
        type=int,
        help="For --suite interchange, stop after this many generated interchange tests and print a partial summary",
    )
    args = parser.parse_args()

    selected_modes = int(bool(args.input)) + int(args.all) + int(bool(args.suite))
    if selected_modes != 1:
        parser.error("Pass exactly one of a single input file, --all, or --suite.")
    if args.input and not args.kind:
        parser.error("Single-input mode requires --kind {c,linux}.")
    if args.kind and not args.input:
        parser.error("--kind is only valid with a single input file.")
    if args.stop_after is not None and args.stop_after <= 0:
        parser.error("--stop-after must be a positive integer.")
    if args.stop_after is not None and args.suite != "interchange":
        parser.error("--stop-after is only valid with --suite interchange.")

    if args.suite == "interchange":
        try:
            return run_interchange_suite(args.stop_after)
        except ComparisonError as error:
            print(error, file=sys.stderr, flush=True)
            return 1

    if args.suite:
        if args.suite in {"c", "linux"}:
            try:
                return run_generated_strength_suite(gather_suite_tests(args.suite))
            except ComparisonError as error:
                print(error, file=sys.stderr, flush=True)
                return 1
        paths = gather_suite_tests(args.suite)
        single_input = None
    else:
        if args.all:
            try:
                return run_generated_strength_suite(gather_all_tests())
            except ComparisonError as error:
                print(error, file=sys.stderr, flush=True)
                return 1
        else:
            paths = []
            single_input = Path(args.input).resolve()
    failures: list[str] = []

    iter_paths = paths if single_input is None else [single_input]
    for path in iter_paths:
        try:
            if single_input is None:
                matches, _, _ = compare_file(path)
            else:
                matches, _, _ = compare_litmus(path, args.kind)
        except ComparisonError as error:
            print(format_status(path, False), flush=True)
            print(error, file=sys.stderr, flush=True)
            failures.append(format_status(path, False).split(": ", 1)[0])
            continue

        print(format_status(path, matches), flush=True)
        if not matches:
            failures.append(format_status(path, False).split(": ", 1)[0])

    print_summary(len(iter_paths), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
