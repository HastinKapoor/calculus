#!/usr/bin/env python3
import argparse
import re
from pathlib import Path


THREAD_HEADER_RE = re.compile(r"^\s*(P\d+)\b")


def parse_op_line(line: str):
    """
    If line looks like Op(...), return (op_name, open_idx, close_idx, args_list).
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


def toggle_language(language: str) -> str:
    if language == "C":
        return "Linux"
    if language == "Linux":
        return "C"
    return language


def language_index_for_args(args_list):
    if len(args_list) >= 4:
        return 3
    return None


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Produce all combinations of a litmus test by toggling the language "
            "for every operation in each thread."
        )
    )
    parser.add_argument("input", help="Input litmus file")
    parser.add_argument("output_dir", help="Directory to write generated litmus files")
    args = parser.parse_args()

    inp = Path(args.input)
    out_dir = Path(args.output_dir)
    if not inp.is_file():
        raise SystemExit(f"Input not found: {inp}")

    out_dir.mkdir(parents=True, exist_ok=True)
    lines = inp.read_text().splitlines(keepends=True)

    occurrences_by_thread = {}
    thread_order = []
    current_thread = None

    for line_index, line in enumerate(lines):
        header_match = THREAD_HEADER_RE.match(line)
        if header_match and "{" in line:
            current_thread = header_match.group(1)
            if current_thread not in occurrences_by_thread:
                occurrences_by_thread[current_thread] = []
                thread_order.append(current_thread)
            continue

        if current_thread is None:
            continue
        if line.strip() == "}":
            current_thread = None
            continue

        parsed = parse_op_line(line)
        if not parsed:
            continue
        _, open_idx, close_idx, args_list = parsed
        language_index = language_index_for_args(args_list)
        if language_index is None:
            continue
        if args_list[language_index] not in {"C", "Linux"}:
            continue
        occurrences_by_thread[current_thread].append(
            {
                "line_index": line_index,
                "open_idx": open_idx,
                "close_idx": close_idx,
                "args": args_list,
                "language_index": language_index,
            }
        )

    toggle_threads = [
        thread_name
        for thread_name in thread_order
        if occurrences_by_thread.get(thread_name)
    ]
    if not toggle_threads:
        print("No thread operations with C/Linux language found; nothing generated.")
        return

    if len(toggle_threads) > 20:
        print(
            f"Warning: {len(toggle_threads)} toggleable threads -> "
            f"{2 ** len(toggle_threads)} combinations."
        )

    stem = inp.stem
    combination_count = 1 << len(toggle_threads)
    for mask in range(combination_count):
        new_lines = list(lines)
        for bit_index, thread_name in enumerate(toggle_threads):
            should_toggle = (mask >> bit_index) & 1
            for occurrence in occurrences_by_thread[thread_name]:
                args_copy = list(occurrence["args"])
                if should_toggle:
                    lang_idx = occurrence["language_index"]
                    args_copy[lang_idx] = toggle_language(args_copy[lang_idx])
                new_args_text = ", ".join(args_copy)
                line_index = occurrence["line_index"]
                new_lines[line_index] = (
                    lines[line_index][: occurrence["open_idx"] + 1]
                    + new_args_text
                    + lines[line_index][occurrence["close_idx"] :]
                )
        bitstring = format(mask, f"0{len(toggle_threads)}b")
        out_path = out_dir / f"{stem}_combo_{bitstring}.litmus"
        out_path.write_text("".join(new_lines))
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
