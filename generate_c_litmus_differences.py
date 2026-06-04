#!/usr/bin/env python3
"""
Stream-generate bounded translated C litmus tests, compare `rc11_test.py` and
`calculus_test.py`, and persist only mismatches.

The phrase "all possible litmus tests" is only finite after fixing a canonical
search space. This script uses the following default search space:

- instructions are only translated C `Read(...)`, `Write(...)`, and `Fence(...)`
  events
- total instruction count is bounded by the positional `bound`
- threads are all non-empty ordered partitions of the total instruction count,
  filtered by the requested minimum/maximum thread count
- locations are canonically named `x0`, `x1`, ... and location-renaming
  duplicates are pruned
- write values range over `{0, 1}`
- read orders are `Relaxed`, `Acquire`
- write orders are `Relaxed`, `Release`
- fence orders are `Acquire`, `Release`, `Acq_Rel`, `SEQ_CST`
- final constraints enumerate every non-empty subset of observed registers and,
  by default, final locations, with values from `{0, 1}`

Each test is written to one temporary file, checked immediately, and discarded
 unless the verdicts differ.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


READ_ORDERS = ("Relaxed", "Acquire")
WRITE_ORDERS = ("Relaxed", "Release")
FENCE_ORDERS = ("Acquire", "Release", "Acq_Rel", "SEQ_CST")


@dataclass(frozen=True)
class ReadInstr:
    location_index: int
    order: str


@dataclass(frozen=True)
class WriteInstr:
    location_index: int
    value: int
    order: str


@dataclass(frozen=True)
class FenceInstr:
    order: str


Instruction = ReadInstr | WriteInstr | FenceInstr


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate bounded translated C litmus tests, run rc11_test.py and "
            "calculus_test.py on each generated test, and store only mismatches."
        )
    )
    parser.add_argument(
        "bound",
        type=int,
        help="Maximum total number of instructions in a generated litmus test",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="generated_differences",
        help="Directory where mismatching tests and a manifest are written",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Generate tests with exactly `bound` instructions instead of 1..bound",
    )
    parser.add_argument(
        "--min-threads",
        type=int,
        default=1,
        help="Minimum number of threads to use (default: 1)",
    )
    parser.add_argument(
        "--max-threads",
        type=int,
        default=None,
        help="Maximum number of threads to use (default: bound)",
    )
    parser.add_argument(
        "--max-locations",
        type=int,
        default=None,
        help="Maximum number of canonical locations to use (default: bound)",
    )
    parser.add_argument(
        "--no-final-locations",
        action="store_true",
        help="Do not enumerate final-state constraints on locations such as `x0=1`",
    )
    parser.add_argument(
        "--allow-empty-exists",
        action="store_true",
        help="Include an unconstrained `exists ()` clause",
    )
    parser.add_argument(
        "--stop-after",
        type=int,
        default=None,
        help="Stop after generating this many litmus tests (useful for smoke tests)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Print a progress line every N generated tests",
    )
    return parser.parse_args()


def iter_instruction_counts(bound: int, exact: bool):
    if exact:
        yield bound
        return

    for count in range(1, bound + 1):
        yield count


def iter_compositions(total: int, parts: int):
    if parts == 1:
        yield (total,)
        return

    for first in range(1, total - parts + 2):
        for rest in iter_compositions(total - first, parts - 1):
            yield (first,) + rest


def is_canonical_location_usage(instructions: tuple[Instruction, ...]) -> bool:
    next_expected = 0
    seen: set[int] = set()

    for instruction in instructions:
        if isinstance(instruction, FenceInstr):
            continue
        loc = instruction.location_index
        if loc in seen:
            continue
        if loc != next_expected:
            return False
        seen.add(loc)
        next_expected += 1

    return True


def iter_instruction_sequences(length: int, max_locations: int, values: tuple[int, ...]):
    choices: list[Instruction] = []
    for order in FENCE_ORDERS:
        choices.append(FenceInstr(order))
    for loc in range(max_locations):
        for order in READ_ORDERS:
            choices.append(ReadInstr(loc, order))
        for order in WRITE_ORDERS:
            for value in values:
                choices.append(WriteInstr(loc, value, order))

    for sequence in itertools.product(choices, repeat=length):
        if is_canonical_location_usage(sequence):
            yield sequence


def split_threads(sequence: tuple[Instruction, ...], composition: tuple[int, ...]):
    threads = []
    index = 0
    for size in composition:
        threads.append(sequence[index : index + size])
        index += size
    return threads


def is_memory_instruction(instruction: Instruction) -> bool:
    return isinstance(instruction, (ReadInstr, WriteInstr))


def thread_has_well_placed_fences(thread: tuple[Instruction, ...]) -> bool:
    for index, instruction in enumerate(thread):
        if not isinstance(instruction, FenceInstr):
            continue

        has_memory_before = any(
            is_memory_instruction(candidate) for candidate in thread[:index]
        )
        has_memory_after = any(
            is_memory_instruction(candidate) for candidate in thread[index + 1 :]
        )
        if not (has_memory_before and has_memory_after):
            return False

    return True


def threads_are_valid(threads: list[tuple[Instruction, ...]]) -> bool:
    return all(thread_has_well_placed_fences(thread) for thread in threads)


def sequence_has_write(sequence: tuple[Instruction, ...]) -> bool:
    return any(isinstance(instruction, WriteInstr) for instruction in sequence)


def reads_target_written_locations(sequence: tuple[Instruction, ...]) -> bool:
    written_locations = {
        instruction.location_index
        for instruction in sequence
        if isinstance(instruction, WriteInstr)
    }
    for instruction in sequence:
        if isinstance(instruction, ReadInstr) and instruction.location_index not in written_locations:
            return False
    return True


def format_instruction(instruction: Instruction, read_index: int | None) -> str:
    if isinstance(instruction, FenceInstr):
        return f"  Fence(None, None, {instruction.order}, C);"

    location = f"x{instruction.location_index}"
    if isinstance(instruction, ReadInstr):
        return f"  Read({location}, None, {instruction.order}, C, r{read_index});"

    return f"  Write({location}, {instruction.value}, {instruction.order}, C);"


def collect_observables(
    sequence: tuple[Instruction, ...],
    include_final_locations: bool,
) -> list[str]:
    observables = []
    read_counter = 0

    for instruction in sequence:
        if isinstance(instruction, ReadInstr):
            observables.append(f"r{read_counter}")
            read_counter += 1

    if include_final_locations:
        used_locations = []
    for instruction in sequence:
        if isinstance(instruction, FenceInstr):
            continue
        location = f"x{instruction.location_index}"
        if location not in used_locations:
            used_locations.append(location)
        observables.extend(used_locations)

    return observables


def iter_constraint_clauses(
    observables: list[str],
    values: tuple[int, ...],
    allow_empty_exists: bool,
):
    if not observables:
        yield {}
        return

    start_size = 0 if allow_empty_exists else 1
    for size in range(start_size, len(observables) + 1):
        for subset in itertools.combinations(observables, size):
            if not subset:
                yield {}
                continue
            for assignment in itertools.product(values, repeat=size):
                yield dict(zip(subset, assignment))


def render_exists_clause(clause: dict[str, int]) -> str:
    if not clause:
        return "exists ()"
    pieces = [f"{name}={value}" for name, value in clause.items()]
    return "exists (" + " /\\ ".join(pieces) + ")"


def render_litmus(
    threads: list[tuple[Instruction, ...]],
    clause: dict[str, int],
) -> str:
    lines: list[str] = []
    read_counter = 0

    for thread_index, thread in enumerate(threads):
        lines.append(f"P{thread_index} {{")
        for instruction in thread:
            if isinstance(instruction, ReadInstr):
                lines.append(format_instruction(instruction, read_counter))
                read_counter += 1
            else:
                lines.append(format_instruction(instruction, None))
        lines.append("}")
        lines.append("")

    lines.append(render_exists_clause(clause))
    lines.append("")
    return "\n".join(lines)


def parse_verdict(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        if ": " not in line:
            continue
        _, verdict = line.rsplit(": ", 1)
        verdict = verdict.strip()
        if verdict in {"Allowed", "Forbidden"}:
            return verdict
    return None


def run_checker(script_path: Path, litmus_file: Path) -> tuple[str, str]:
    result = subprocess.run(
        [sys.executable, str(script_path), str(litmus_file)],
        capture_output=True,
        text=True,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        details = stderr or stdout or f"exit code {result.returncode}"
        return "ERROR", details

    verdict = parse_verdict(stdout)
    if verdict is None:
        return "ERROR", stdout or stderr or "could not parse checker output"

    return verdict, stdout


def ensure_tools(root: Path) -> tuple[Path, Path]:
    rc11_script = root / "rc11_test.py"
    calculus_script = root / "calculus_test.py"

    missing = [path for path in (rc11_script, calculus_script) if not path.is_file()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Required checker script not found: {missing_text}")

    return rc11_script, calculus_script


def persist_mismatch(
    output_dir: Path,
    manifest_path: Path,
    litmus_text: str,
    rc11_verdict: str,
    calculus_verdict: str,
    total_instructions: int,
    thread_sizes: tuple[int, ...],
    clause: dict[str, int],
):
    digest = hashlib.sha1(litmus_text.encode("utf-8")).hexdigest()[:12]
    file_name = (
        f"diff_n{total_instructions}_t{'-'.join(map(str, thread_sizes))}_{digest}.litmus"
    )
    litmus_path = output_dir / file_name
    litmus_path.write_text(litmus_text, encoding="utf-8")

    record = {
        "file": file_name,
        "rc11_test": rc11_verdict,
        "calculus_test": calculus_verdict,
        "instructions": total_instructions,
        "thread_sizes": list(thread_sizes),
        "constraint": clause,
    }
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    if args.bound <= 0:
        print("`bound` must be positive.", file=sys.stderr)
        return 2
    if args.min_threads <= 0:
        print("`--min-threads` must be positive.", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parent
    rc11_script, calculus_script = ensure_tools(root)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.jsonl"
    temp_litmus = output_dir / ".current_generated.litmus"

    max_threads = args.max_threads or args.bound
    if args.min_threads > max_threads:
        print("`--min-threads` cannot exceed `--max-threads`.", file=sys.stderr)
        return 2
    max_locations = args.max_locations or args.bound
    values = (0, 1)

    generated = 0
    mismatches = 0

    try:
        for total_instructions in iter_instruction_counts(args.bound, args.exact):
            thread_min = min(total_instructions, args.min_threads)
            thread_limit = min(total_instructions, max_threads)

            for thread_count in range(thread_min, thread_limit + 1):
                for composition in iter_compositions(total_instructions, thread_count):
                    for sequence in iter_instruction_sequences(
                        total_instructions,
                        max_locations,
                        values,
                    ):
                        if not sequence_has_write(sequence):
                            continue
                        if not reads_target_written_locations(sequence):
                            continue
                        threads = split_threads(sequence, composition)
                        if not threads_are_valid(threads):
                            continue
                        observables = collect_observables(
                            sequence,
                            include_final_locations=not args.no_final_locations,
                        )

                        for clause in iter_constraint_clauses(
                            observables,
                            values,
                            allow_empty_exists=args.allow_empty_exists,
                        ):
                            litmus_text = render_litmus(threads, clause)
                            temp_litmus.write_text(litmus_text, encoding="utf-8")

                            rc11_verdict, rc11_details = run_checker(rc11_script, temp_litmus)
                            calculus_verdict, calculus_details = run_checker(
                                calculus_script,
                                temp_litmus,
                            )

                            generated += 1
                            if args.progress_every > 0 and generated % args.progress_every == 0:
                                print(
                                    f"Generated {generated} tests, mismatches {mismatches}",
                                    flush=True,
                                )

                            if "ERROR" in {rc11_verdict, calculus_verdict}:
                                print("Checker error on generated test:", file=sys.stderr)
                                print(litmus_text, file=sys.stderr)
                                print(f"rc11_test.py: {rc11_details}", file=sys.stderr)
                                print(
                                    f"calculus_test.py: {calculus_details}",
                                    file=sys.stderr,
                                )
                                return 1

                            if rc11_verdict != calculus_verdict:
                                mismatches += 1
                                persist_mismatch(
                                    output_dir=output_dir,
                                    manifest_path=manifest_path,
                                    litmus_text=litmus_text,
                                    rc11_verdict=rc11_verdict,
                                    calculus_verdict=calculus_verdict,
                                    total_instructions=total_instructions,
                                    thread_sizes=composition,
                                    clause=clause,
                                )
                                print(
                                    f"Mismatch {mismatches}: rc11={rc11_verdict}, "
                                    f"calculus={calculus_verdict}",
                                    flush=True,
                                )

                            if args.stop_after is not None and generated >= args.stop_after:
                                print(
                                    f"Stopped after {generated} generated tests; "
                                    f"mismatches found: {mismatches}",
                                    flush=True,
                                )
                                return 0
    finally:
        if temp_litmus.exists():
            temp_litmus.unlink()

    print(f"Generated {generated} tests total; mismatches found: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
