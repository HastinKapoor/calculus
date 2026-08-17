#!/usr/bin/env python3
"""Generate source-level memory-order variants for C and Linux litmus tests."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


LINUX_LOAD_PAIRS = {
    "READ_ONCE": "smp_load_acquire",
    "smp_load_acquire": "READ_ONCE",
}
LINUX_STORE_PAIRS = {
    "WRITE_ONCE": "smp_store_release",
    "smp_store_release": "WRITE_ONCE",
}
C_LOAD_ORDERS = {
    "memory_order_relaxed": "memory_order_acquire",
    "memory_order_acquire": "memory_order_relaxed",
}
C_STORE_ORDERS = {
    "memory_order_relaxed": "memory_order_release",
    "memory_order_release": "memory_order_relaxed",
}


@dataclass(frozen=True)
class Occurrence:
    line_index: int
    open_index: int
    close_index: int
    replacement_value: str


def split_args(args_text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in args_text:
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def parse_call_in_line(line: str, func_name: str) -> tuple[int, int, list[str]] | None:
    needle = f"{func_name}("
    func_index = line.find(needle)
    if func_index == -1:
        return None
    open_index = func_index + len(func_name)
    depth = 0
    close_index = -1
    for index in range(open_index, len(line)):
        char = line[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close_index = index
                break
    if close_index == -1:
        return None
    args_text = line[open_index + 1 : close_index]
    return func_index, open_index, split_args(args_text)


def remove_leading_deref(expr: str) -> str | None:
    stripped = expr.strip()
    if not stripped.startswith("*"):
        return None
    return stripped[1:].strip()


def add_leading_deref(expr: str) -> str:
    stripped = expr.strip()
    if stripped.startswith("*"):
        return stripped
    return f"*{stripped}"


def build_linux_replacement(func_name: str, args: list[str]) -> str | None:
    if func_name == "READ_ONCE" and len(args) == 1:
        pointer = remove_leading_deref(args[0])
        if pointer is None:
            return None
        return f"smp_load_acquire({pointer})"
    if func_name == "smp_load_acquire" and len(args) == 1:
        return f"READ_ONCE({add_leading_deref(args[0])})"
    if func_name == "WRITE_ONCE" and len(args) == 2:
        pointer = remove_leading_deref(args[0])
        if pointer is None:
            return None
        return f"smp_store_release({pointer}, {args[1]})"
    if func_name == "smp_store_release" and len(args) == 2:
        return f"WRITE_ONCE({add_leading_deref(args[0])}, {args[1]})"
    return None


def collect_linux_occurrences(lines: list[str]) -> list[Occurrence]:
    occurrences: list[Occurrence] = []
    for line_index, line in enumerate(lines):
        for func_name in (*LINUX_LOAD_PAIRS.keys(), *LINUX_STORE_PAIRS.keys()):
            parsed = parse_call_in_line(line, func_name)
            if parsed is None:
                continue
            func_index, _, args = parsed
            replacement = build_linux_replacement(func_name, args)
            if replacement is None:
                continue
            close_index = line.find(")", func_index)
            if close_index == -1:
                continue
            occurrences.append(
                Occurrence(
                    line_index=line_index,
                    open_index=func_index,
                    close_index=close_index + 1,
                    replacement_value=replacement,
                )
            )
            break
    return occurrences


def collect_c_occurrences(lines: list[str]) -> list[Occurrence]:
    occurrences: list[Occurrence] = []
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        load_parsed = parse_call_in_line(line, "atomic_load_explicit")
        if load_parsed is not None:
            _, _, args = load_parsed
            if len(args) >= 2 and args[-1] in C_LOAD_ORDERS:
                order = args[-1]
                order_index = line.rfind(order)
                if order_index != -1:
                    occurrences.append(
                        Occurrence(
                            line_index=line_index,
                            open_index=order_index,
                            close_index=order_index + len(order),
                            replacement_value=C_LOAD_ORDERS[order],
                        )
                    )
            continue

        store_parsed = parse_call_in_line(line, "atomic_store_explicit")
        if store_parsed is not None:
            _, _, args = store_parsed
            if len(args) >= 3 and args[-1] in C_STORE_ORDERS:
                order = args[-1]
                order_index = line.rfind(order)
                if order_index != -1:
                    occurrences.append(
                        Occurrence(
                            line_index=line_index,
                            open_index=order_index,
                            close_index=order_index + len(order),
                            replacement_value=C_STORE_ORDERS[order],
                        )
                    )
            continue

        if not stripped.endswith(";") or "==" in stripped or stripped.startswith("return"):
            continue
        if "=" not in stripped:
            continue
        lhs, rhs = stripped[:-1].split("=", 1)
        lhs = lhs.strip()
        rhs = rhs.strip()
        if not lhs.startswith("*"):
            continue
        pointer = remove_leading_deref(lhs)
        if pointer is None or not rhs:
            continue
        indent_width = len(line) - len(line.lstrip())
        indent = line[:indent_width]
        replacement = f"{indent}atomic_store_explicit({pointer}, {rhs}, memory_order_release);"
        occurrences.append(
            Occurrence(
                line_index=line_index,
                open_index=indent_width,
                close_index=len(line.rstrip("\n")),
                replacement_value=replacement.rstrip("\n"),
            )
        )
    return occurrences


def apply_occurrences(lines: list[str], occurrences: list[Occurrence], mask: int) -> list[str]:
    new_lines = list(lines)
    for bit_index, occurrence in enumerate(occurrences):
        if ((mask >> bit_index) & 1) == 0:
            continue
        line = new_lines[occurrence.line_index]
        new_lines[occurrence.line_index] = (
            line[: occurrence.open_index]
            + occurrence.replacement_value
            + line[occurrence.close_index :]
        )
    return new_lines


def generate_variants(input_file: Path, output_dir: Path, kind: str) -> int:
    lines = input_file.read_text().splitlines(keepends=True)
    occurrences = (
        collect_c_occurrences(lines) if kind == "c" else collect_linux_occurrences(lines)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_file.stem
    if not occurrences:
        out_path = output_dir / f"{stem}_combo_0.litmus"
        out_path.write_text("".join(lines))
        print(f"Wrote {out_path}")
        return 0

    for mask in range(1 << len(occurrences)):
        out_path = output_dir / f"{stem}_combo_{format(mask, f'0{len(occurrences)}b')}.litmus"
        out_path.write_text("".join(apply_occurrences(lines, occurrences, mask)))
        print(f"Wrote {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate source-level memory-order variants for a litmus test."
    )
    parser.add_argument("input", help="Input source litmus file")
    parser.add_argument("output_dir", help="Directory to write generated source litmus files")
    parser.add_argument("--kind", required=True, choices=["c", "linux"])
    args = parser.parse_args()

    input_file = Path(args.input)
    if not input_file.is_file():
        raise SystemExit(f"Input not found: {input_file}")
    return generate_variants(input_file, Path(args.output_dir), args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
