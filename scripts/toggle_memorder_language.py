#!/usr/bin/env python3

import argparse
import re
from pathlib import Path

TARGET_ORDERS = {"Release", "Acquire", "SEQ_CST"}

def parse_op_line(line: str):
    """
    If line looks like Op(...), return (op_name, open_idx, close_idx, args_list)
    otherwise return None.
    """
    if '(' not in line or ')' not in line:
        return None
    open_idx = line.find('(')
    close_idx = line.rfind(')')
    head = line[:open_idx].strip()
    m = re.search(r'(\w+)\s*$', head)
    if not m:
        return None
    op = m.group(1)
    args_text = line[open_idx+1:close_idx]
    args = [a.strip() for a in args_text.split(',')]
    return op, open_idx, close_idx, args

def toggle_language(lang: str) -> str:
    if lang == 'C':
        return 'Linux'
    if lang == 'Linux':
        return 'C'
    return lang

def main():
    p = argparse.ArgumentParser(description="Produce all combinations of a litmus test toggling language for each Acquire/Release/SEQ_CST op")
    p.add_argument('input', help='Input litmus file')
    p.add_argument('output_dir', help='Directory to write generated litmus files')
    args = p.parse_args()

    inp = Path(args.input)
    out_dir = Path(args.output_dir)
    if not inp.is_file():
        raise SystemExit(f"Input not found: {inp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = inp.read_text().splitlines(keepends=True)

    occurrences = []
    for li, line in enumerate(lines):
        parsed = parse_op_line(line)
        if not parsed:
            continue
        op, oidx, cidx, args_list = parsed
        # need at least 4 arguments so args_list[2] is strength and args_list[3] is language
        if len(args_list) >= 4:
            strength = args_list[2]
            lang = args_list[3]
            if strength in TARGET_ORDERS and lang in {'C', 'Linux'}:
                occurrences.append((li, oidx, cidx, args_list))

    if not occurrences:
        print("No Acquire/Release/SEQ_CST operations with C/Linux language found; nothing generated.")
        return

    n = len(occurrences)
    if n > 20:
        print(f"Warning: {n} toggle points -> {2**n} combinations (large). Proceeding anyway.")

    stem = inp.stem
    # generate all 2^n combinations (including original / no-change)
    for mask in range(0, 1 << n):
        new_lines = list(lines)
        # apply toggles according to bits in mask; bit k corresponds to occurrences[k]
        for k in range(n):
            li, oidx, cidx, args_list = occurrences[k]
            args_copy = list(args_list)
            # if bit is 1 toggle language for this occurrence
            if (mask >> k) & 1:
                args_copy[3] = toggle_language(args_copy[3])
            # otherwise leave as-is
            new_args_text = ', '.join(args_copy)
            new_line = lines[li][:oidx+1] + new_args_text + lines[li][cidx:]
            new_lines[li] = new_line
        bitstr = format(mask, f'0{n}b')
        out_path = out_dir / f"{stem}_combo_{bitstr}.litmus"
        out_path.write_text(''.join(new_lines))
        print(f"Wrote {out_path}")

if __name__ == "__main__":
    main()
