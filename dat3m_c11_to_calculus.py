"""
Convert Dat3M C11-style litmus files (folder: litmus/Dat3M/C11) into the
calculus_test format with the following guarantees:
 - initial comments removed
 - opening brace placed on same line as process header (P0(...){ )
 - local variable declarations inside processes removed
 - local variables that conflict across processes are renamed and the renaming
   is applied inside the process body and in the final constraint (exists ...)
Usage:
  python3 dat3m_c11_to_calculus.py INPUT_DIR OUTPUT_DIR
"""
import re
import argparse
from pathlib import Path
from collections import Counter, defaultdict

def remove_comments(text: str) -> str:
    # remove C-style block comments /* ... */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    # remove OCaml-style (* ... *) if present
    while True:
        new = re.sub(r'\(\*.*?\*\)', '', text, flags=re.S)
        if new == text:
            break
        text = new
    # remove C++ style //...
    text = re.sub(r'//.*$', '', text, flags=re.M)
    return text

def remove_initial_header_lines(text: str) -> str:
    # Remove any leading blank lines and any initial non-process header lines until the first P/T header
    lines = text.splitlines()
    out = []
    seen_proc = False
    proc_re = re.compile(r'^\s*[PT]\d+\s*\(')
    for ln in lines:
        if not seen_proc and proc_re.search(ln):
            seen_proc = True
            out.append(ln)
        elif not seen_proc:
            # skip
            continue
        else:
            out.append(ln)
    return '\n'.join(out)

def bring_brace_up(text: str) -> str:
    # Put opening brace on same line as process header: "P0(...) \n {" -> "P0(...){"
    return re.sub(r'^(\s*(?:P|T)\d+\s*\([^)]*\))\s*\n\s*\{', r'\1{', text, flags=re.M)

def collapse_top_level_brace_blocks(text: str) -> str:
    # Collapse isolated { ... } blocks (used for init) to single-line { ... }
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == '{':
            j = i + 1
            inner = []
            while j < len(lines) and lines[j].strip() != '}':
                inner.append(lines[j].strip())
                j += 1
            if j < len(lines) and lines[j].strip() == '}':
                inner_s = ' '.join([ln for ln in inner if ln])
                out.append('{' + ((' ' + inner_s + ' ') if inner_s else '') + '}')
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)

_decl_single_re = re.compile(r'^\s*(?:int|unsigned|long|short|char|bool)\b[^;]*\b(?P<name>[A-Za-z_]\w*)\s*(?:=\s*[^;]+)?\s*;\s*$')
_decl_multi_re = re.compile(r'^\s*(?:int|unsigned|long|short|char|bool)\b\s+(?P<rest>[^;]+)\s*;\s*$')

def remove_local_decls_and_collect(body: str):
    lines = body.splitlines()
    new_lines = []
    locals_found = []
    for ln in lines:
        m = _decl_single_re.match(ln)
        if m:
            locals_found.append(m.group('name'))
            continue
        m2 = _decl_multi_re.match(ln)
        if m2:
            rest = m2.group('rest')
            parts = [p.strip() for p in rest.split(',')]
            for p in parts:
                name_m = re.match(r'([A-Za-z_]\w*)', p)
                if name_m:
                    locals_found.append(name_m.group(1))
            continue
        new_lines.append(ln)
    return '\n'.join(new_lines), locals_found

def replace_word(text: str, old: str, new: str):
    return re.sub(r'\b' + re.escape(old) + r'\b', new, text)

_process_block_re = re.compile(r'^(?P<header>(?P<pname>[PT])(?P<tid>\d+)\s*\((?P<args>[^)]*)\))\s*\{\s*(?P<body>.*?)\s*\}', re.M | re.S)

def process_locals_and_rename(text: str) -> str:
    blocks = []
    for m in _process_block_re.finditer(text):
        blocks.append({
            'header': m.group('header'),
            'tid': int(m.group('tid')),
            'args': m.group('args').strip(),
            'body': m.group('body'),
            'span': (m.start(), m.end())
        })
    if not blocks:
        return text

    locals_by_tid = {}
    new_bodies = {}
    for b in blocks:
        new_body, names = remove_local_decls_and_collect(b['body'])
        locals_by_tid[b['tid']] = names
        new_bodies[b['tid']] = new_body

    all_names = []
    for names in locals_by_tid.values():
        all_names.extend(names)
    counts = Counter(all_names)
    conflicts = {n for n,c in counts.items() if c > 1}
    rename_map = defaultdict(dict)
    if conflicts:
        for tid, names in locals_by_tid.items():
            for name in names:
                if name in conflicts:
                    rename_map[tid][name] = f"{name}_T{tid}"
        # apply renames inside process bodies
        for tid, body in new_bodies.items():
            rm = rename_map.get(tid, {})
            nb = body
            for old, new in rm.items():
                nb = replace_word(nb, old, new)
            new_bodies[tid] = nb
        # update final exists lines (remove numeric prefixes as well)
        def update_exists_line(ln: str) -> str:
            if ln.strip().lower().startswith('exists'):
                res = ln
                # first remove any numeric "N:" prefixes like "1:r0"
                res = re.sub(r'\b\d+\s*:\s*', '', res)
                # apply renames for patterns plain var names
                for tid, mapping in rename_map.items():
                    for old, new in mapping.items():
                        res = replace_word(res, old, new)
                return res
            return ln
        # perform replacement on whole text for exists lines
        lines = text.splitlines()
        lines = [update_exists_line(ln) for ln in lines]
        text = '\n'.join(lines)

    # reconstruct text by replacing blocks (do in reverse order)
    out = text
    for b in sorted(blocks, key=lambda x: x['span'][0], reverse=True):
        tid = b['tid']
        start, end = b['span']
        new_block = f"{b['header']}{{\n{new_bodies[tid]}\n}}"
        out = out[:start] + new_block + out[end:]
    return out

def normalize_whitespace(text: str) -> str:
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

def convert_file(text: str) -> str:
    # remove comments everywhere
    t = remove_comments(text)
    # drop initial header lines until first process header
    t = remove_initial_header_lines(t)
    # ensure brace on same line
    t = bring_brace_up(t)
    # collapse isolated init blocks
    t = collapse_top_level_brace_blocks(t)
    # remove locals and rename conflicts, update exists clauses
    t = process_locals_and_rename(t)
    # normalize whitespace
    t = normalize_whitespace(t)
    return t

def process_dir(inp_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(inp_dir.glob('*.litmus')):
        out_path = out_dir / f.name
        print(f"Converting {f} -> {out_path}")
        out_path.write_text(convert_file(f.read_text()))

def main():
    p = argparse.ArgumentParser(description="Convert Dat3M C11 litmus files to calculus format")
    p.add_argument('input', help='Input directory containing .litmus files (e.g. litmus/Dat3M/C11)')
    p.add_argument('output', help='Output directory for converted files (e.g. litmus/)')
    args = p.parse_args()

    inp = Path(args.input)
    outp = Path(args.output)
    if not inp.is_dir():
        print(f"Input directory not found: {inp}")
        return
    process_dir(inp, outp)

if __name__ == "__main__":
    main()
# filepath: /home/kapoorh/Documents/calculus/dat3m_c11_to_calculus.py
"""
Convert Dat3M C11-style litmus files (folder: litmus/Dat3M/C11) into the
calculus_test format with the following guarantees:
 - initial comments removed
 - opening brace placed on same line as process header (P0(...){ )
 - local variable declarations inside processes removed
 - local variables that conflict across processes are renamed and the renaming
   is applied inside the process body and in the final constraint (exists ...)
Usage:
  python3 dat3m_c11_to_calculus.py INPUT_DIR OUTPUT_DIR
"""
import re
import argparse
from pathlib import Path
from collections import Counter, defaultdict

def remove_comments(text: str) -> str:
    # remove C-style block comments /* ... */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    # remove OCaml-style (* ... *) if present
    while True:
        new = re.sub(r'\(\*.*?\*\)', '', text, flags=re.S)
        if new == text:
            break
        text = new
    # remove C++ style //...
    text = re.sub(r'//.*$', '', text, flags=re.M)
    return text

def remove_initial_header_lines(text: str) -> str:
    # Remove any leading blank lines and any initial non-process header lines until the first P/T header
    lines = text.splitlines()
    out = []
    seen_proc = False
    proc_re = re.compile(r'^\s*[PT]\d+\s*\(')
    for ln in lines:
        if not seen_proc and proc_re.search(ln):
            seen_proc = True
            out.append(ln)
        elif not seen_proc:
            # skip
            continue
        else:
            out.append(ln)
    return '\n'.join(out)

def bring_brace_up(text: str) -> str:
    # Put opening brace on same line as process header: "P0(...) \n {" -> "P0(...){"
    return re.sub(r'^(\s*(?:P|T)\d+\s*\([^)]*\))\s*\n\s*\{', r'\1{', text, flags=re.M)

def collapse_top_level_brace_blocks(text: str) -> str:
    # Collapse isolated { ... } blocks (used for init) to single-line { ... }
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == '{':
            j = i + 1
            inner = []
            while j < len(lines) and lines[j].strip() != '}':
                inner.append(lines[j].strip())
                j += 1
            if j < len(lines) and lines[j].strip() == '}':
                inner_s = ' '.join([ln for ln in inner if ln])
                out.append('{' + ((' ' + inner_s + ' ') if inner_s else '') + '}')
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)

_decl_single_re = re.compile(r'^\s*(?:int|unsigned|long|short|char|bool)\b[^;]*\b(?P<name>[A-Za-z_]\w*)\s*(?:=\s*[^;]+)?\s*;\s*$')
_decl_multi_re = re.compile(r'^\s*(?:int|unsigned|long|short|char|bool)\b\s+(?P<rest>[^;]+)\s*;\s*$')

def remove_local_decls_and_collect(body: str):
    lines = body.splitlines()
    new_lines = []
    locals_found = []
    for ln in lines:
        m = _decl_single_re.match(ln)
        if m:
            locals_found.append(m.group('name'))
            continue
        m2 = _decl_multi_re.match(ln)
        if m2:
            rest = m2.group('rest')
            parts = [p.strip() for p in rest.split(',')]
            for p in parts:
                name_m = re.match(r'([A-Za-z_]\w*)', p)
                if name_m:
                    locals_found.append(name_m.group(1))
            continue
        new_lines.append(ln)
    return '\n'.join(new_lines), locals_found

def replace_word(text: str, old: str, new: str):
    return re.sub(r'\b' + re.escape(old) + r'\b', new, text)

_process_block_re = re.compile(r'^(?P<header>(?P<pname>[PT])(?P<tid>\d+)\s*\((?P<args>[^)]*)\))\s*\{\s*(?P<body>.*?)\s*\}', re.M | re.S)

def process_locals_and_rename(text: str) -> str:
    blocks = []
    for m in _process_block_re.finditer(text):
        blocks.append({
            'header': m.group('header'),
            'tid': int(m.group('tid')),
            'args': m.group('args').strip(),
            'body': m.group('body'),
            'span': (m.start(), m.end())
        })
    if not blocks:
        return text

    locals_by_tid = {}
    new_bodies = {}
    for b in blocks:
        new_body, names = remove_local_decls_and_collect(b['body'])
        locals_by_tid[b['tid']] = names
        new_bodies[b['tid']] = new_body

    all_names = []
    for names in locals_by_tid.values():
        all_names.extend(names)
    counts = Counter(all_names)
    conflicts = {n for n,c in counts.items() if c > 1}
    rename_map = defaultdict(dict)
    if conflicts:
        for tid, names in locals_by_tid.items():
            for name in names:
                if name in conflicts:
                    rename_map[tid][name] = f"{name}_T{tid}"
        # apply renames inside process bodies
        for tid, body in new_bodies.items():
            rm = rename_map.get(tid, {})
            nb = body
            for old, new in rm.items():
                nb = replace_word(nb, old, new)
            new_bodies[tid] = nb
        # update final exists lines (remove numeric prefixes as well)
        def update_exists_line(ln: str) -> str:
            if ln.strip().lower().startswith('exists'):
                res = ln
                # first remove any numeric "N:" prefixes like "1:r0"
                res = re.sub(r'\b\d+\s*:\s*', '', res)
                # apply renames for patterns plain var names
                for tid, mapping in rename_map.items():
                    for old, new in mapping.items():
                        res = replace_word(res, old, new)
                return res
            return ln
        # perform replacement on whole text for exists lines
        lines = text.splitlines()
        lines = [update_exists_line(ln) for ln in lines]
        text = '\n'.join(lines)

    # reconstruct text by replacing blocks (do in reverse order)
    out = text
    for b in sorted(blocks, key=lambda x: x['span'][0], reverse=True):
        tid = b['tid']
        start, end = b['span']
        new_block = f"{b['header']}{{\n{new_bodies[tid]}\n}}"
        out = out[:start] + new_block + out[end:]
    return out

def normalize_whitespace(text: str) -> str:
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

def convert_file(text: str) -> str:
    # remove comments everywhere
    t = remove_comments(text)
    # drop initial header lines until first process header
    t = remove_initial_header_lines(t)
    # ensure brace on same line
    t = bring_brace_up(t)
    # collapse isolated init blocks
    t = collapse_top_level_brace_blocks(t)
    # remove locals and rename conflicts, update exists clauses
    t = process_locals_and_rename(t)
    # normalize whitespace
    t = normalize_whitespace(t)
    return t

def process_dir(inp_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(inp_dir.glob('*.litmus')):
        out_path = out_dir / f.name
        print(f"Converting {f} -> {out_path}")
        out_path.write_text(convert_file(f.read_text()))

def main():
    p = argparse.ArgumentParser(description="Convert Dat3M C11 litmus files to calculus format")
    p.add_argument('input', help='Input directory containing .litmus files (e.g. litmus/Dat3M/C11)')
    p.add_argument('output', help='Output directory for converted files (e.g. litmus/)')
    args = p.parse_args()

    inp = Path(args.input)
    outp = Path(args.output)
    if not inp.is_dir():
        print(f"Input directory not found: {inp}")
        return
    process_dir(inp, outp)

if __name__ == "__main__":
    main()