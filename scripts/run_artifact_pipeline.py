#!/usr/bin/env python3
"""Compare herd7 outcomes against the calculus pipeline."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

try:
    from toggle_memorder_strength import canonical_digest
except ModuleNotFoundError:  # pragma: no cover - allows repo-root imports in tests/snippets
    from scripts.toggle_memorder_strength import canonical_digest


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


class ComparisonError(RuntimeError):
    pass


def print_summary(total: int, failures: list[str]) -> None:
    print("Summary:", flush=True)
    print(f"  Total tests: {total}", flush=True)
    print(f"  Mismatches: {len(failures)}", flush=True)
    if failures:
        print("  Mismatch list:", flush=True)
        for label in failures:
            print(f"    {label}", flush=True)


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


def translate_source_tests(paths: list[Path], translated_root: Path) -> dict[str, list[Path]]:
    digest_map: dict[str, list[Path]] = defaultdict(list)
    for source_litmus in paths:
        rel_path = source_litmus.resolve().relative_to(LITMUS_ROOT.resolve())
        translated_path = translated_root / rel_path
        kind = infer_source_kind(source_litmus)
        translate_source_file(source_litmus, translated_path, kind)
        digest_map[canonical_digest(translated_path.read_text())].append(source_litmus)
    return digest_map


def choose_source_for_generated_variant(
    generated_litmus: Path, digest_map: dict[str, list[Path]]
) -> Path:
    digest = canonical_digest(generated_litmus.read_text())
    candidates = digest_map.get(digest)
    if not candidates:
        raise ComparisonError(f"No matching source litmus found for generated variant: {generated_litmus}")
    return sorted(candidates)[0]


def run_generated_strength_suite(paths: list[Path]) -> int:
    with tempfile.TemporaryDirectory(prefix="run-artifact-all-") as tmpdir:
        tmp_root = Path(tmpdir)
        translated_root = tmp_root / "translated"
        strength_root = tmp_root / "strength"

        digest_map = translate_source_tests(paths, translated_root)
        require_success(
            run_command([str(TOGGLE_STRENGTH), str(translated_root), str(strength_root)]),
            "toggle_memorder_strength",
        )

        generated_paths = sorted(strength_root.rglob("*.litmus"))
        failures: list[str] = []
        for generated_litmus in generated_paths:
            try:
                source_litmus = choose_source_for_generated_variant(generated_litmus, digest_map)
                kind = infer_source_kind(source_litmus)
                herd_result = run_herd_c(source_litmus) if kind == "c" else run_herd_linux(source_litmus)
                calculus_result = evaluate_litmus(generated_litmus)
                matches = herd_result == calculus_result
            except ComparisonError as error:
                label = generated_litmus.resolve().relative_to(strength_root.resolve()).as_posix()
                print(f"generated/{label}: MISMATCH", flush=True)
                print(error, file=sys.stderr, flush=True)
                failures.append(f"generated/{label}")
                continue

            label = generated_litmus.resolve().relative_to(strength_root.resolve()).as_posix()
            print(f"generated/{label}: {'MATCH' if matches else 'MISMATCH'}", flush=True)
            if not matches:
                failures.append(f"generated/{label}")

        print_summary(len(generated_paths), failures)
        return 1 if failures else 0


def run_interchange_suite(stop_after: int | None = None) -> int:
    if not CONVERTED_ROOT.is_dir():
        raise ComparisonError(f"Converted test directory not found: {CONVERTED_ROOT}")

    with tempfile.TemporaryDirectory(prefix="run-artifact-interchange-") as tmpdir:
        tmp_root = Path(tmpdir)
        representative_dir, representative_groups = stage_representative_inputs(
            CONVERTED_ROOT, tmp_root / "representative"
        )
        strength_dir = tmp_root / "strength"
        language_dir = tmp_root / "language"

        require_success(
            run_command([str(TOGGLE_STRENGTH), str(representative_dir), str(strength_dir)]),
            "toggle_memorder_strength",
        )
        require_success(
            run_command([str(TOGGLE_LANGUAGE), str(strength_dir), str(language_dir)]),
            "toggle_memorder_language",
        )

        groups: dict[Path, list[tuple[str, Path]]] = defaultdict(list)
        for litmus_path in sorted(language_dir.rglob("*.litmus")):
            rel_path = litmus_path.relative_to(language_dir)
            group_base, bits = parse_combo_stem(rel_path)
            group_rel = rel_path.with_name(f"{group_base}.litmus")
            groups[group_rel].append((bits, litmus_path))

        failures: list[str] = []
        processed = 0
        for group_rel, variants in sorted(groups.items()):
            if stop_after is not None and processed >= stop_after:
                break
            ordered_variants = sorted(variants, key=lambda item: item[0])
            baseline_bits = "0" * len(ordered_variants[0][0])
            if ordered_variants[0][0] != baseline_bits:
                raise ComparisonError(
                    f"Expected baseline variant {baseline_bits} for {group_rel}, "
                    f"found {ordered_variants[0][0]}"
                )

            verdicts = {
                bits: evaluate_litmus(litmus_path)
                for bits, litmus_path in ordered_variants[1:]
            }
            observed = set(verdicts.values())
            label = group_rel.as_posix()
            matches = len(observed) <= 1
            print(f"interchange/{label}: {'MATCH' if matches else 'MISMATCH'}", flush=True)
            if not matches:
                failures.append(f"interchange/{label}")
                details = ", ".join(
                    f"combo_{bits}={verdict}" for bits, verdict in sorted(verdicts.items())
                )
                print(details, file=sys.stderr, flush=True)
            processed += 1

        print(
            "Interchange representatives: "
            f"{len(representative_groups)} groups from "
            f"{sum(len(paths) for paths in representative_groups.values())} inputs.",
            flush=True,
        )
        if stop_after is not None and processed < len(groups):
            print(f"Stopped early after {processed} interchange tests.", flush=True)
        print_summary(processed, failures)
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
