#!/usr/bin/env python3
"""Run strength/language toggles and check calculus consistency."""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CALCULUS = REPO_ROOT / "calculus.py"
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


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)


def require_success(result: subprocess.CompletedProcess[str], description: str) -> None:
    if result.returncode == 0:
        return
    if result.stdout:
        print(result.stdout, end="", file=sys.stderr)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    raise SystemExit(f"{description} failed with exit code {result.returncode}.")


def parse_combo_stem(path: Path) -> tuple[str, str]:
    match = COMBO_SUFFIX_RE.fullmatch(path.stem)
    if not match:
        raise ValueError(f"Expected combo suffix in generated file: {path}")
    return match.group("base"), match.group("bits")


def representative_key(path: Path) -> str:
    parts = path.stem.split("+")
    while len(parts) > 1 and parts[-1] in MEMORDER_VARIANT_TOKENS:
        parts.pop()
    return "+".join(parts)


def infer_kind_from_path(path: Path) -> str:
    parts = path.resolve().parts
    if "litmus" in parts:
        litmus_index = parts.index("litmus")
        if litmus_index + 1 < len(parts):
            family = parts[litmus_index + 1]
            if family == "c":
                return "c"
            if family in {"Kernel", "paulmckrcu"}:
                return "linux"
    raise SystemExit(f"Could not infer source language from path: {path}")


def iter_baseline_inputs(input_dir: Path) -> list[Path]:
    if input_dir.name in {"converted", "litmus"}:
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


def translate_representative_inputs(
    input_dir: Path,
    representative_groups: dict[Path, list[Path]],
    translated_dir: Path,
) -> Path:
    translated_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, variants in sorted(representative_groups.items()):
        destination = translated_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        preferred = input_dir / rel_path
        representative = preferred if preferred in variants else variants[0]
        translator = C_TRANSLATOR if infer_kind_from_path(representative) == "c" else LINUX_TRANSLATOR
        require_success(
            run_command([sys.executable, str(translator), str(representative), "-o", str(destination)]),
            f"translation for {representative}",
        )
    return translated_dir


def original_test_name(group_rel: Path) -> str:
    strength_base, _ = parse_combo_stem(group_rel)
    source_dir = group_rel.parent.parent
    source_rel = source_dir / f"{strength_base}.litmus"
    return source_rel.as_posix()


def evaluate_litmus(path: Path) -> str:
    result = run_command([sys.executable, str(CALCULUS), str(path)])
    require_success(result, f"calculus evaluation for {path}")

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise SystemExit(f"calculus.py produced no output for {path}")

    final_line = lines[-1]
    _, separator, verdict = final_line.rpartition(":")
    if separator != ":":
        raise SystemExit(f"Could not parse calculus verdict for {path}: {final_line}")

    verdict = verdict.strip()
    if verdict not in {"Allowed", "Forbidden"}:
        raise SystemExit(f"Unexpected calculus verdict for {path}: {verdict}")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Translate source litmus tests, expand them with memory-order and "
            "language toggles, run calculus.py on the generated variants, and "
            "report tests whose non-baseline language variants disagree."
        )
    )
    parser.add_argument(
        "input_dir",
        help="Directory of source .litmus files to analyze",
    )
    parser.add_argument(
        "--keep-workdir",
        help="Optional directory where intermediate strength/language outputs should be kept",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")

    if args.keep_workdir:
        workdir = Path(args.keep_workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="memorder-toggle-check-")
        workdir = Path(cleanup.name)

    representative_dir, representative_groups = stage_representative_inputs(
        input_dir, workdir / "representative"
    )
    translated_dir = translate_representative_inputs(
        input_dir, representative_groups, workdir / "translated"
    )
    strength_dir = workdir / "strength"
    language_dir = workdir / "language"

    require_success(
        run_command([str(TOGGLE_STRENGTH), str(translated_dir), str(strength_dir)]),
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

    if not groups:
        raise SystemExit(f"No language-toggle outputs found under {language_dir}")

    failing_tests: dict[str, list[str]] = defaultdict(list)

    for group_rel, variants in sorted(groups.items()):
        ordered_variants = sorted(variants, key=lambda item: item[0])
        baseline_bits = "0" * len(ordered_variants[0][0])
        if ordered_variants[0][0] != baseline_bits:
            raise SystemExit(
                f"Expected baseline variant {baseline_bits} for {group_rel}, "
                f"found {ordered_variants[0][0]}"
            )

        test_name = original_test_name(group_rel)
        generated_variants = ", ".join(
            f"combo_{bits}" for bits, _ in ordered_variants
        )
        print(f"{test_name} <- {group_rel.as_posix()}: {generated_variants}")

        alternate_results = []
        for bits, litmus_path in ordered_variants[1:]:
            alternate_results.append((bits, evaluate_litmus(litmus_path)))

        observed = {result for _, result in alternate_results}
        if len(observed) > 1:
            strength_variant = group_rel.as_posix()
            summary = ", ".join(f"combo_{bits}={result}" for bits, result in alternate_results)
            failing_tests[test_name].append(f"{strength_variant}: {summary}")

    total_tests = len({original_test_name(group_rel) for group_rel in groups})

    print(
        f"Checked {total_tests} representative tests "
        f"(from {sum(len(paths) for paths in representative_groups.values())} inputs)."
    )
    if not failing_tests:
        print("All tests passed.")
        return 0

    print("Tests with disagreeing non-baseline outputs:")
    for test_name in sorted(failing_tests):
        print(test_name)
        for detail in failing_tests[test_name]:
            print(f"  {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
