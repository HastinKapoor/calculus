#!/usr/bin/env python3
import argparse
import hashlib
import re
from pathlib import Path


READ_ORDERS = ("Relaxed", "Acquire")
WRITE_ORDERS = ("Relaxed", "Release")
PRESERVED_IDENTIFIERS = {
    "Acquire",
    "C",
    "Fence",
    "Linux",
    "None",
    "P0",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "P8",
    "P9",
    "Read",
    "Relaxed",
    "Release",
    "SEQ_CST",
    "Write",
    "atomic_int",
    "bool",
    "char",
    "double",
    "exists",
    "float",
    "if",
    "int",
    "long",
    "short",
    "signed",
    "unsigned",
    "volatile",
}


def parse_op_line(line: str):
    """
    If line looks like Op(...); return (op_name, open_idx, close_idx, args_list).
    Otherwise return None.
    """
    if "(" not in line or ")" not in line:
        return None
    open_idx = line.find("(")
    close_idx = line.rfind(")")
    head = line[:open_idx].strip()
    match = re.search(r"(\w+)\s*$", head)
    if not match:
        return None
    op = match.group(1)
    args_text = line[open_idx + 1 : close_idx]
    args = [arg.strip() for arg in args_text.split(",")]
    return op, open_idx, close_idx, args


def choices_for_op(op: str, order: str):
    if op == "Read" and order in READ_ORDERS:
        return READ_ORDERS
    if op == "Write" and order in WRITE_ORDERS:
        return WRITE_ORDERS
    return None


def order_index_for_op(op: str, args_list):
    if op == "Read" and len(args_list) >= 5:
        return 2
    if op == "Write" and len(args_list) >= 4:
        return 2
    return None


def canonicalize_text(text: str) -> str:
    identifier_map = {}
    next_identifier_id = 0

    def replace_identifier(match: re.Match[str]) -> str:
        nonlocal next_identifier_id
        token = match.group(0)
        if token in PRESERVED_IDENTIFIERS:
            return token
        if token not in identifier_map:
            identifier_map[token] = f"v{next_identifier_id}"
            next_identifier_id += 1
        return identifier_map[token]

    canonical_lines = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == "{}":
            continue
        thread_match = re.fullmatch(r"(P\d+)\s*\([^)]*\)\s*\{", stripped)
        if thread_match:
            canonical_lines.append(f"{thread_match.group(1)} {{")
            continue
        parsed = parse_op_line(stripped)
        if parsed:
            op, _, _, args_list = parsed
            if op == "Write" and len(args_list) == 4:
                stripped = f"Write({', '.join(args_list + ['None'])});"
        normalized = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", replace_identifier, stripped)
        normalized = re.sub(r"\s+", " ", normalized)
        canonical_lines.append(normalized)
    return "\n".join(canonical_lines)


def canonical_digest(text: str) -> str:
    return hashlib.sha256(canonicalize_text(text).encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Produce all combinations of Relaxed/Acquire reads and "
            "Relaxed/Release writes for a converted litmus test."
        )
    )
    parser.add_argument("input", nargs="?", help="Input converted litmus file")
    parser.add_argument("output_dir", nargs="?", help="Directory to write generated litmus files")
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="Print the canonical digest for a litmus file instead of generating variants",
    )
    args = parser.parse_args()

    if not args.input:
        raise SystemExit("An input file is required.")

    inp = Path(args.input)
    if not inp.is_file():
        raise SystemExit(f"Input not found: {inp}")

    if args.canonical:
        if args.output_dir is not None:
            raise SystemExit("--canonical does not accept an output directory.")
        print(canonical_digest(inp.read_text()))
        return

    if not args.output_dir:
        raise SystemExit("An output directory is required unless --canonical is used.")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = inp.read_text().splitlines(keepends=True)

    occurrences = []
    for line_index, line in enumerate(lines):
        parsed = parse_op_line(line)
        if not parsed:
            continue
        op, open_idx, close_idx, args_list = parsed
        order_index = order_index_for_op(op, args_list)
        if order_index is None:
            continue
        order_choices = choices_for_op(op, args_list[order_index])
        if not order_choices:
            continue
        occurrences.append(
            {
                "line_index": line_index,
                "open_idx": open_idx,
                "close_idx": close_idx,
                "args": args_list,
                "order_index": order_index,
                "choices": order_choices,
            }
        )

    combination_count = 1 << len(occurrences)
    if len(occurrences) > 20:
        print(
            f"Warning: {len(occurrences)} toggle points -> {combination_count} combinations."
        )

    stem = inp.stem
    if not occurrences:
        out_path = out_dir / f"{stem}_combo_0.litmus"
        out_path.write_text("".join(lines))
        print(f"Wrote {out_path}")
        return

    for mask in range(combination_count):
        new_lines = list(lines)
        for bit_index, occurrence in enumerate(occurrences):
            args_copy = list(occurrence["args"])
            args_copy[occurrence["order_index"]] = occurrence["choices"][(mask >> bit_index) & 1]
            new_args_text = ", ".join(args_copy)
            line_index = occurrence["line_index"]
            new_lines[line_index] = (
                lines[line_index][: occurrence["open_idx"] + 1]
                + new_args_text
                + lines[line_index][occurrence["close_idx"] :]
            )
        bitstring = format(mask, f"0{len(occurrences)}b")
        out_path = out_dir / f"{stem}_combo_{bitstring}.litmus"
        out_path.write_text("".join(new_lines))
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
