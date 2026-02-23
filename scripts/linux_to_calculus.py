"""
Translate simple Linux kernel litmus-style macros into the calculus_test format,
normalize structure, remove comments, remove local variable declarations inside
process blocks, and rename local variables that appear in more than one process.

Usage:
    python3 linux_to_calculus.py path/to/file.litmus
    python3 linux_to_calculus.py path/to/file.litmus -o out.litmus
    python3 linux_to_calculus.py path/to/litmus-tests -o out_dir
"""
import re
import argparse
from pathlib import Path
import os
from collections import Counter, defaultdict

# Patterns for macro translations
READ_ASSIGN_RE = re.compile(r'\b(r\d+)\s*=\s*READ_ONCE\s*\(\s*([^)]+?)\s*\)\s*;')
READ_STANDALONE_RE = re.compile(r'\bREAD_ONCE\s*\(\s*([^)]+?)\s*\)\s*;')
WRITE_ONCE_RE = re.compile(r'\bWRITE_ONCE\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*;')

SMP_LOAD_ACQ_ASSIGN_RE = re.compile(r'\b(r\d+)\s*=\s*smp_load_acquire\s*\(\s*([^)]+?)\s*\)\s*;')
SMP_STORE_REL_RE = re.compile(r'\bsmp_store_release\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*;')
SMP_MB_RE = re.compile(r'\bsmp_mb\s*\(\s*\)\s*;')  # full memory barrier
SMP_WMB_RE = re.compile(r'\bsmp_wmb\s*\(\s*\)\s*;')  # write memory barrier

def replace_read_assign(match):
    reg = match.group(1)
    loc = match.group(2).strip()
    return f"Read({loc}, None, Relaxed, Linux, {reg});"

def replace_read_standalone(match):
    loc = match.group(1).strip()
    return f"Read({loc}, None, Relaxed, Linux, None);"

def replace_write_once(match):
    loc = match.group(1).strip()
    val = match.group(2).strip()
    return f"Write({loc}, {val}, Relaxed, Linux, None);"

def replace_smp_load_acq_assign(match):
    reg = match.group(1)
    loc = match.group(2).strip()
    return f"Read({loc}, None, Acquire, Linux, {reg});"

def replace_smp_store_rel(match):
    loc = match.group(1).strip()
    val = match.group(2).strip()
    return f"Write({loc}, {val}, Release, Linux, None);"

def replace_smp_mb(match):
    # Translate Linux full memory barrier to a Seq_Cst fence in calculus format
    return "Fence(None, None, SEQ_CST, Linux, None);"

def replace_smp_wmb(match):
    # Translate Linux write memory barrier to a WMB fence in calculus format
    return "Fence(None, None, WMB, Linux, None);"

def convert_text(text: str) -> str:
    # Apply macro translations (order matters)
    text = SMP_MB_RE.sub(replace_smp_mb, text)
    text = SMP_WMB_RE.sub(replace_smp_wmb, text)
    text = SMP_LOAD_ACQ_ASSIGN_RE.sub(replace_smp_load_acq_assign, text)
    text = READ_ASSIGN_RE.sub(replace_read_assign, text)
    text = WRITE_ONCE_RE.sub(replace_write_once, text)
    text = SMP_STORE_REL_RE.sub(replace_smp_store_rel, text)
    text = READ_STANDALONE_RE.sub(replace_read_standalone, text)
    return text

# Structural cleanup & formatting for calculus_test parser
def remove_comments(text: str) -> str:
    """
    Remove OCaml-style comments (* ... *), C-style block comments /* ... */,
    and C++-style line comments // ... . Run OCaml-style removal repeatedly
    to handle multiple occurrences.
    """
    # Remove C-style block comments /* ... */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)

    # Remove OCaml-style block comments (* ... *) repeatedly (non-greedy)
    while True:
        new_text = re.sub(r'\(\*.*?\*\)', '', text, flags=re.S)
        if new_text == text:
            break
        text = new_text

    # Remove C++ style line comments //
    text = re.sub(r'//.*$', '', text, flags=re.M)

    return text

def remove_first_nonempty_line(text: str) -> str:
    # Remove the first non-empty line (test name/header)
    lines = text.splitlines()
    out = []
    removed = False
    for ln in lines:
        if not removed and ln.strip() != '':
            removed = True
            continue
        out.append(ln)
    return '\n'.join(out)

def bring_brace_up(text: str) -> str:
    # Ensure opening brace is on same line as Pn(...) header: "P0(...)\n{" -> "P0(...){"
    text = re.sub(r'^(\s*(?:P|T)\d+\s*\([^)]*\))\s*\n\s*\{', r'\1{', text, flags=re.M)
    return text

def collapse_top_level_brace_blocks(text: str) -> str:
    # Collapse standalone brace blocks (often used for initializations) into single-line { ... }
    lines = text.splitlines()
    i = 0
    out_lines = []
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == '{':
            # find matching closing brace on its own line
            j = i + 1
            content_lines = []
            while j < len(lines) and lines[j].strip() != '}':
                content_lines.append(lines[j].strip())
                j += 1
            if j < len(lines) and lines[j].strip() == '}':
                # collapse
                inner = ' '.join([c for c in content_lines if c != ''])
                out_lines.append('{' + ((' ' + inner + ' ') if inner else '') + '}')
                i = j + 1
                continue
            else:
                # unmatched, fallthrough
                out_lines.append(ln)
                i += 1
                continue
        else:
            out_lines.append(ln)
            i += 1
    return '\n'.join(out_lines)

def normalize_whitespace(text: str) -> str:
    # Remove trailing spaces and normalize blank lines (max one)
    lines = [ln.rstrip() for ln in text.splitlines()]
    out = []
    prev_blank = False
    for ln in lines:
        if ln.strip() == '':
            if not prev_blank:
                out.append('')
            prev_blank = True
        else:
            out.append(ln)
            prev_blank = False
    return '\n'.join(out).strip() + '\n'

def format_litmus_structure(text: str) -> str:
    # Remove comments first
    text = remove_comments(text)
    # Remove test name (first non-empty line)
    text = remove_first_nonempty_line(text)
    # Ensure braces for Pn(...) are on same line
    text = bring_brace_up(text)
    # Collapse top-level init blocks like:
    # {
    #   x = 1;
    # }
    # into single line: { x = 1; }
    text = collapse_top_level_brace_blocks(text)
    # Normalize whitespace
    text = normalize_whitespace(text)
    return text

def _find_process_blocks(text: str):
    """
    Find process blocks and return list of dicts:
      { 'header': header_text, 'tid': int, 'args': arg_text, 'body': body_text, 'span': (start,end) }
    """
    pattern = re.compile(r'^(?P<header>(?P<pname>[PT])(?P<tid>\d+)\s*\((?P<args>[^)]*)\))\s*\{\s*(?P<body>.*?)\s*\}', re.M | re.S)
    blocks = []
    for m in pattern.finditer(text):
        blocks.append({
            'header': m.group('header'),
            'pname': m.group('pname'),
            'tid': int(m.group('tid')),
            'args': m.group('args').strip(),
            'body': m.group('body'),
            'span': (m.start(), m.end())
        })
    return blocks

def _remove_local_decls_and_collect(body: str):
    """
    Remove local variable declarations like:
      int r0;
      int r0 = 0;
      unsigned long foo;
    Return new_body, list_of_local_names
    """
    lines = body.splitlines()
    new_lines = []
    local_names = []
    decl_re = re.compile(r'^\s*(?:int|unsigned|long|short|char|bool)\b[^;]*\b(?P<name>[A-Za-z_]\w*)\s*(?:=\s*[^;]+)?\s*;\s*$')
    for ln in lines:
        m = decl_re.match(ln)
        if m:
            local_names.append(m.group('name'))
            # drop declaration line
            continue
        # also handle simple 'int r0, r1;' form
        m2 = re.match(r'^\s*(?:int|unsigned|long|short|char|bool)\b\s+(?P<rest>[^;]+)\s*;\s*$', ln)
        if m2:
            rest = m2.group('rest')
            # split by commas and extract names (ignore initializers)
            parts = [p.strip() for p in rest.split(',')]
            extracted = []
            for p in parts:
                name_m = re.match(r'(?P<name>[A-Za-z_]\w*)', p)
                if name_m:
                    extracted.append(name_m.group('name'))
            if extracted:
                local_names.extend(extracted)
                continue
        new_lines.append(ln)
    return '\n'.join(new_lines), local_names

def _replace_word(text: str, old: str, new: str):
    return re.sub(r'\b' + re.escape(old) + r'\b', new, text)

def process_locals_and_rename(text: str) -> str:
    """
    Removes local variable declarations inside each process and renames locals
    that appear in more than one process. Updates references in process bodies
    and the final 'exists' line (only thread-prefixed references "T:r" or "N:r" are updated).
    """
    blocks = _find_process_blocks(text)
    if not blocks:
        return text

    # Map tid -> body start/end indices in original text for safe replacement later
    # Process each block to remove local decls and collect names
    locals_by_tid = {}
    new_bodies = {}
    for b in blocks:
        new_body, names = _remove_local_decls_and_collect(b['body'])
        locals_by_tid[b['tid']] = names
        new_bodies[b['tid']] = new_body

    # Find names that occur in more than one thread
    all_names = []
    for names in locals_by_tid.values():
        all_names.extend(names)
    counts = Counter(all_names)
    conflicts = {name for name, c in counts.items() if c > 1}
    if conflicts:
        # Build rename map: for each tid and each conflicting name, rename to name_Ttid
        rename_map = defaultdict(dict)  # tid -> {old: new}
        for tid, names in locals_by_tid.items():
            for name in names:
                if name in conflicts:
                    rename_map[tid][name] = f"{name}_T{tid}"

        # Apply renames inside each process body
        for tid, body in new_bodies.items():
            rm = rename_map.get(tid, {})
            new_text = body
            # Replace each old->new with word boundaries
            for old, new in rm.items():
                new_text = _replace_word(new_text, old, new)
            new_bodies[tid] = new_text

        # Update exists line(s): replace occurrences of 'N:name' where N matches tid
        def _replace_in_exists(exists_text: str) -> str:
            # replace patterns like '1:r0' or '1: r0'
            for tid, mapping in rename_map.items():
                for old, new in mapping.items():
                    # replace "tid:old" with "tid:new"
                    exists_text = re.sub(r'(?P<prefix>\b' + re.escape(str(tid)) + r'\s*:\s*)' + re.escape(old) + r'\b',
                                         r'\1' + new, exists_text)
                    # also replace "Ttid:old" forms if present (unlikely), e.g., "1:r0" handled above
            return exists_text

        # perform replacements: find exists lines and update
        def _update_exists_lines(txt: str):
            lines = txt.splitlines()
            updated = []
            for ln in lines:
                if ln.strip().lower().startswith('exists'):
                    ln = _replace_in_exists(ln)
                updated.append(ln)
            return '\n'.join(updated)

        text = _update_exists_lines(text)

    # Reconstruct text with updated process bodies (and removed declarations)
    # We'll do safe replacement using the spans captured earlier: iterate blocks in reverse order so indices remain valid
    out = text
    for b in sorted(blocks, key=lambda x: x['span'][0], reverse=True):
        tid = b['tid']
        start, end = b['span']
        # Build replacement block: header + '{' + new_body + '}'
        new_block = f"{b['header']}{{\n{new_bodies[tid]}\n}}"
        out = out[:start] + new_block + out[end:]
    return out

def strip_thread_prefixes_in_exists(text: str) -> str:
    """
    Remove numeric thread prefixes like "1:" from the 'exists' line(s).
    e.g. "exists (1:r0=1 /\ 2:r1=0)" -> "exists (r0=1 /\ r1=0)"
    """
    def fix_line(ln: str) -> str:
        if ln.strip().lower().startswith('exists'):
            # remove occurrences of '<digits>:' possibly with surrounding whitespace
            return re.sub(r'\b\d+\s*:\s*', '', ln)
        return ln

    return '\n'.join(fix_line(ln) for ln in text.splitlines())

def convert_and_format(text: str) -> str:
    """
    First remove comments and normalize structure, then perform macro translations
    and local-variable processing. This order prevents macro substitution inside
    comments from producing stray tokens.
    """
    # Remove comments and normalize structure first
    t = format_litmus_structure(text)
    # Now translate macros
    t = convert_text(t)
    # Process locals / rename conflicts
    t = process_locals_and_rename(t)
    # Strip any thread-number prefixes from exists lines
    t = strip_thread_prefixes_in_exists(t)
    # Final normalization
    t = normalize_whitespace(t)
    return t

def process_file(inp: Path, outp: Path):
    txt = inp.read_text()
    converted = convert_and_format(txt)
    outp.write_text(converted)

def main():
    p = argparse.ArgumentParser(description="Convert Linux-style litmus macros to calculus_test format and normalize structure")
    p.add_argument('input', help='Path to input litmus file or directory containing .litmus files')
    p.add_argument('-o', '--output', help='Path for translated output (file or directory). If omitted: for a file, prints to stdout; for a directory, writes files with suffix _converted.litmus in the same dir', default=None)
    args = p.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        print(f"Input not found: {inp}")
        return

    if inp.is_dir():
        # batch convert all .litmus files
        files = sorted(inp.glob('*.litmus'))
        if not files:
            print(f"No .litmus files found in directory: {inp}")
            return
        out_dir = None
        if args.output:
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            out_name = f.stem + '_converted.litmus'
            out_path = (out_dir / out_name) if out_dir else (f.parent / out_name)
            process_file(f, out_path)
            print(f"Wrote {out_path}")
    else:
        # single file
        txt = inp.read_text()
        converted = convert_and_format(txt)
        if args.output:
            outp = Path(args.output)
            if outp.is_dir():
                outp = outp / (inp.name)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(converted)
            print(f"Wrote {outp}")
        else:
            print(converted)

if __name__ == "__main__":
    main()