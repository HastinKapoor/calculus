#!/usr/bin/env python3
"""Run strength/language toggles and check calculus consistency."""

import argparse
import re
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


def print_variant_summary(total_variants: int, matches: int, mismatches: int) -> None:
    print("Summary:")
    print(f"  Total variants tested: {total_variants}")
    print(f"  Matches: {matches}")
    print(f"  Mismatches: {mismatches}")


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


def collect_input_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix != ".litmus":
            raise SystemExit(f"Expected a .litmus file, got: {input_path}")
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.rglob("*.litmus"))
    raise SystemExit(f"Input not found: {input_path}")


def select_representative_inputs(input_paths: list[Path], root_path: Path) -> dict[Path, list[Path]]:
    groups: dict[Path, list[Path]] = defaultdict(list)
    for litmus_path in input_paths:
        rel_path = Path(litmus_path.name) if root_path.is_file() else litmus_path.relative_to(root_path)
        key = rel_path.with_name(f"{representative_key(rel_path)}.litmus")
        groups[key].append(litmus_path)
    return groups


def translate_representative_input(
    representative: Path,
    rel_path: Path,
    kind: str,
    translated_dir: Path,
) -> Path:
    destination = translated_dir / rel_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    translator = C_TRANSLATOR if kind == "c" else LINUX_TRANSLATOR
    require_success(
        run_command([sys.executable, str(translator), str(representative), "-o", str(destination)]),
        f"translation for {representative}",
    )
    return destination


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
        "input",
        help="Source .litmus file or directory of source .litmus files to analyze",
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=["c", "linux"],
        help="Language of the input set",
    )
    parser.add_argument(
        "--keep-workdir",
        help="Optional directory where intermediate strength/language outputs should be kept",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    input_paths = collect_input_paths(input_path)

    if args.keep_workdir:
        workdir = Path(args.keep_workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="memorder-toggle-check-")
        workdir = Path(cleanup.name)

    representative_groups = select_representative_inputs(input_paths, input_path)
    failures: list[str] = []
    tested_variants = 0
    match_count = 0
    mismatch_count = 0

    for group_rel, variants in sorted(representative_groups.items()):
        representative = variants[0]
        test_root = workdir / "per_test" / group_rel.with_suffix("")
        translated_dir = test_root / "translated"
        strength_dir = test_root / "strength"
        language_dir = test_root / "language"

        translate_representative_input(representative, group_rel, args.kind, translated_dir)
        require_success(
            run_command([str(TOGGLE_STRENGTH), str(translated_dir), str(strength_dir)]),
            f"toggle_memorder_strength for {representative}",
        )
        require_success(
            run_command([str(TOGGLE_LANGUAGE), str(strength_dir), str(language_dir)]),
            f"toggle_memorder_language for {representative}",
        )

        groups: dict[Path, list[tuple[str, Path]]] = defaultdict(list)
        for litmus_path in sorted(language_dir.rglob("*.litmus")):
            rel_path = litmus_path.relative_to(language_dir)
            group_base, bits = parse_combo_stem(rel_path)
            generated_group_rel = rel_path.with_name(f"{group_base}.litmus")
            groups[generated_group_rel].append((bits, litmus_path))

        if not groups:
            raise SystemExit(f"No language-toggle outputs found under {language_dir}")

        for generated_group_rel, generated_variants in sorted(groups.items()):
            ordered_variants = sorted(generated_variants, key=lambda item: item[0])
            baseline_bits = "0" * len(ordered_variants[0][0])
            if ordered_variants[0][0] != baseline_bits:
                raise SystemExit(
                    f"Expected baseline variant {baseline_bits} for {generated_group_rel}, "
                    f"found {ordered_variants[0][0]}"
                )

            alternate_results = []
            for bits, litmus_path in ordered_variants[1:]:
                alternate_results.append((bits, evaluate_litmus(litmus_path)))

            tested_variants += 1 + len(ordered_variants[1:])
            observed = {result for _, result in alternate_results}
            label = generated_group_rel.as_posix()
            matches = len(observed) <= 1
            print(f"{label}: {'MATCH' if matches else 'MISMATCH'}", flush=True)
            if not matches:
                failures.append(label)
                mismatch_count += 1
            else:
                match_count += 1
    print_variant_summary(tested_variants, match_count, mismatch_count)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
