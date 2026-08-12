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
THREAD_HEADER_LINE_RE = re.compile(r'^\s*(?:P|T)\d+\s*\([^)]*\)\s*$')
TYPE_CAST_PREFIX_RE = re.compile(
    r'^\(\s*(?:const\s+|volatile\s+|signed\s+|unsigned\s+)*'
    r'(?:intptr_t|uintptr_t|size_t|ssize_t|int|long|short|char|bool|void|struct\s+\w+|union\s+\w+)'
    r'(?:\s+(?:const|volatile|signed|unsigned|long|short|int|char|bool|void))*(?:\s*\*+)*\s*\)\s*'
)
DECL_ASSIGN_RE = re.compile(
    r'^\s*(?P<prefix>(?:const|volatile|signed|unsigned|short|long|int|char|bool|void|intptr_t|uintptr_t|size_t|ssize_t|struct\s+\w+|union\s+\w+)[\w\s\*]*)\s+'
    r'(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<rhs>.+?)\s*;\s*$'
)
DECL_ONLY_RE = re.compile(
    r'^\s*(?P<prefix>(?:const|volatile|signed|unsigned|short|long|int|char|bool|void|intptr_t|uintptr_t|size_t|ssize_t|struct\s+\w+|union\s+\w+)[\w\s\*]*)\s+'
    r'(?P<rest>.+?)\s*;\s*$'
)
READ_RENDER_RE = re.compile(r'Read\([^,]+,\s*None,\s*[^,]+,\s*Linux,\s*(?P<reg>[A-Za-z_]\w*)\);')
SELF_COMPARE_ASSIGN_RIGHT_RE = re.compile(
    r'^(?P<indent>\s*)(?P<lhs>[A-Za-z_]\w*)\s*=\s*\(\s*(?P<expr>.+?)\s*(?P<op>==|!=)\s*(?P=lhs)\s*\)\s*;\s*$'
)
SELF_COMPARE_ASSIGN_LEFT_RE = re.compile(
    r'^(?P<indent>\s*)(?P<lhs>[A-Za-z_]\w*)\s*=\s*\(\s*(?P=lhs)\s*(?P<op>==|!=)\s*(?P<expr>.+?)\s*\)\s*;\s*$'
)
IF_GUARD_RE = re.compile(
    r'^(?P<indent>\s*)if\s*\(\s*(?P<guard>[A-Za-z_]\w*)\s*\)\s*(?P<brace>\{?)\s*$'
)


def is_none_token(value) -> bool:
    return value is None or str(value).strip() == "None"

def _normalize_location(loc_raw: str) -> str:
    """
    Normalize a location argument by removing a single leading '*' (and
    optional surrounding parentheses). Examples:
      '*x'     -> 'x'
      '(*x)'   -> 'x'
      '* ( x )'-> 'x'
    """
    if loc_raw is None:
        return loc_raw
    loc = loc_raw.strip()
    # match forms like '(*x)' first
    m = re.match(r'^\(\s*\*\s*([^)]+?)\s*\)$', loc)
    if m:
        return m.group(1).strip()
    # if starts with asterisk, strip it and any immediate whitespace
    if loc.startswith('*'):
        loc = loc[1:].strip()
        # if wrapped in parentheses after removing '*', unwrap once
        if loc.startswith('(') and loc.endswith(')'):
            loc = loc[1:-1].strip()
    return loc


def split_args(args_str: str):
    args = []
    current = []
    depth = 0
    for ch in args_str:
        if ch == ',' and depth == 0:
            arg = ''.join(current).strip()
            if arg:
                args.append(arg)
            current = []
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        current.append(ch)
    tail = ''.join(current).strip()
    if tail:
        args.append(tail)
    return args


def parse_call(expr: str, call_name: str):
    stripped = expr.strip()
    prefix = f"{call_name}("
    if not stripped.startswith(prefix) or not stripped.endswith(')'):
        return None
    return split_args(stripped[len(call_name) + 1:-1])


def strip_leading_casts(expr: str) -> str:
    current = expr.strip()
    while True:
        match = TYPE_CAST_PREFIX_RE.match(current)
        if not match:
            return current
        current = current[match.end():].strip()


def normalize_location_expression(expr: str) -> str:
    current = expr.strip()
    previous = None
    while current != previous:
        previous = current
        if current.startswith('(') and current.endswith(')'):
            depth = 0
            balanced = True
            for idx, ch in enumerate(current):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0 and idx != len(current) - 1:
                        balanced = False
                        break
            if balanced and depth == 0:
                current = current[1:-1].strip()
                continue
        if current.startswith('*'):
            current = current[1:].strip()
            continue
        stripped_cast = strip_leading_casts(current)
        if stripped_cast != current:
            current = stripped_cast
            continue
    return _normalize_location(current)


def normalize_value_expression(expr: str) -> str:
    current = strip_leading_casts(expr).strip()
    previous = None
    while current != previous:
        previous = current
        if current.startswith('(') and current.endswith(')'):
            depth = 0
            balanced = True
            for idx, ch in enumerate(current):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0 and idx != len(current) - 1:
                        balanced = False
                        break
            if balanced and depth == 0:
                current = current[1:-1].strip()
                continue
        stripped_cast = strip_leading_casts(current)
        if stripped_cast != current:
            current = stripped_cast
            continue
    return current


def split_assignment(stripped: str):
    if '=' not in stripped:
        return None, None
    lhs, rhs = stripped.split('=', 1)
    return lhs.strip(), rhs.strip()


def extract_assigned_name(lhs: str):
    tokens = re.findall(r'[A-Za-z_]\w*', lhs)
    if not tokens:
        return None
    return tokens[-1]


def convert_assignment_line(stripped: str):
    lhs, rhs = split_assignment(stripped.rstrip(';'))
    if lhs is None:
        return None

    register = extract_assigned_name(lhs)
    if register is None:
        return None

    rhs_no_cast = strip_leading_casts(rhs)

    args = parse_call(rhs_no_cast, 'READ_ONCE')
    if args and len(args) >= 1:
        return f"Read({normalize_location_expression(args[0])}, None, Relaxed, Linux, {register});"

    args = parse_call(rhs_no_cast, 'smp_load_acquire')
    if args and len(args) >= 1:
        return f"Read({normalize_location_expression(args[0])}, None, Acquire, Linux, {register});"

    args = parse_call(rhs_no_cast, 'rcu_dereference')
    if args and len(args) >= 1:
        return f"Read({normalize_location_expression(args[0])}, None, Relaxed, Linux, {register});"

    args = parse_call(rhs_no_cast, 'spin_is_locked')
    if args and len(args) >= 1:
        return f"Read({normalize_location_expression(args[0])}, None, Relaxed, Linux, {register});"

    if rhs_no_cast.startswith('*'):
        return f"Read({normalize_location_expression(rhs_no_cast)}, None, Relaxed, Linux, {register});"

    return None


def convert_standalone_line(stripped: str):
    args = parse_call(stripped.rstrip(';'), 'spin_lock')
    if args and len(args) >= 1:
        location = normalize_location_expression(args[0])
        return (
            f"Read({location}, 0, LOCK_READ, Linux, None);\n"
            f"Write({location}, 1, LOCK_WRITE, Linux, None);"
        )

    args = parse_call(stripped.rstrip(';'), 'spin_unlock')
    if args and len(args) >= 1:
        return f"Write({normalize_location_expression(args[0])}, 0, UNLOCK, Linux, None);"

    args = parse_call(stripped.rstrip(';'), 'WRITE_ONCE')
    if args and len(args) >= 2:
        return f"Write({normalize_location_expression(args[0])}, {normalize_value_expression(args[1])}, Relaxed, Linux, None);"

    args = parse_call(stripped.rstrip(';'), 'smp_store_release')
    if args and len(args) >= 2:
        return f"Write({normalize_location_expression(args[0])}, {normalize_value_expression(args[1])}, Release, Linux, None);"

    args = parse_call(stripped.rstrip(';'), 'rcu_assign_pointer')
    if args and len(args) >= 2:
        return f"Write({normalize_location_expression(args[0])}, {normalize_value_expression(args[1])}, Release, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'smp_mb') is not None:
        return "Fence(None, None, SEQ_CST, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'smp_wmb') is not None:
        return "Fence(None, None, WMB, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'smp_rmb') is not None:
        return "Fence(None, None, RMB, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'smp_mb__after_spinlock') is not None:
        return "Fence(None, None, AFTER_SPINLOCK, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'smp_mb__after_unlock_lock') is not None:
        return "Fence(None, None, AFTER_UNLOCK_LOCK, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'synchronize_rcu') is not None:
        return "Fence(None, None, SYNC_RCU, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'rcu_read_lock') is not None:
        return "Fence(None, None, RCU_LOCK, Linux, None);"

    if parse_call(stripped.rstrip(';'), 'rcu_read_unlock') is not None:
        return "Fence(None, None, RCU_UNLOCK, Linux, None);"

    lhs, rhs = split_assignment(stripped.rstrip(';'))
    if lhs is not None and lhs.startswith('*'):
        return f"Write({normalize_location_expression(lhs)}, {normalize_value_expression(rhs)}, Relaxed, Linux, None);"

    args = parse_call(stripped.rstrip(';'), 'READ_ONCE')
    if args and len(args) >= 1:
        return f"Read({normalize_location_expression(args[0])}, None, Relaxed, Linux, None);"

    return None


def convert_text(text: str) -> str:
    converted_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            converted_lines.append(line)
            continue

        converted = convert_assignment_line(stripped)
        if converted is None:
            converted = convert_standalone_line(stripped)

        if converted is None:
            converted_lines.append(line)
        else:
            indent = line[:len(line) - len(line.lstrip())]
            if "\n" in converted:
                converted_lines.extend(
                    indent + converted_line
                    for converted_line in converted.splitlines()
                )
            else:
                converted_lines.append(indent + converted)

    return '\n'.join(converted_lines)

# Structural cleanup & formatting for calculus_test parser
def remove_comments(text: str) -> str:
    """
    Remove OCaml-style comments (* ... *), C-style block comments /* ... */,
    and C++-style line comments // ... . Run OCaml-style removal repeatedly
    to handle multiple occurrences.
    """
    # Remove C-style block comments /* ... */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)

    # Remove OCaml-style block comments only when they begin at the start of a line.
    # This avoids corrupting pointer expressions like "(*u0)" or "(*(intptr_t **)x2)".
    lines = text.splitlines()
    filtered = []
    in_ocaml_comment = False
    for line in lines:
        stripped = line.lstrip()
        if in_ocaml_comment:
            if '*)' in stripped:
                in_ocaml_comment = False
            continue
        if stripped.startswith('(*'):
            if '*)' not in stripped:
                in_ocaml_comment = True
            continue
        filtered.append(line)
    text = '\n'.join(filtered)

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
    # Collapse only the simple pre-thread initialization block into single-line { ... }.
    lines = text.splitlines()
    i = 0
    out_lines = []
    seen_thread = False
    while i < len(lines):
        ln = lines[i]
        if THREAD_HEADER_LINE_RE.match(ln.strip()):
            seen_thread = True

        if not seen_thread and ln.strip() == '{':
            j = i + 1
            content_lines = []
            simple_block = True
            while j < len(lines):
                stripped = lines[j].strip()
                if stripped == '}':
                    break
                if stripped == '' or '{' in stripped or '}' in stripped or THREAD_HEADER_LINE_RE.match(stripped):
                    simple_block = False
                    break
                content_lines.append(stripped)
                j += 1

            if simple_block and j < len(lines) and lines[j].strip() == '}':
                inner = ' '.join([c for c in content_lines if c != ''])
                out_lines.append('{' + ((' ' + inner + ' ') if inner else '') + '}')
                i = j + 1
                continue

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

def _resolve_alias_value(value: str, aliases):
    current = value.strip()
    for _ in range(8):
        if current in aliases:
            nxt = aliases[current].strip()
            if nxt == current:
                break
            current = nxt
            continue
        break
    return current

def _extract_declared_names(rest: str):
    names = []
    for part in rest.split(','):
        tokens = re.findall(r'[A-Za-z_]\w*', part)
        if tokens:
            names.append(tokens[-1])
    return names

def _remove_local_decls_and_collect(body: str):
    """
    Remove local declarations, recording their names and simple initializer aliases
    so later references can be substituted into translated event lines.
    """
    lines = body.splitlines()
    new_lines = []
    local_names = []
    aliases = {}
    for ln in lines:
        m = DECL_ASSIGN_RE.match(ln)
        if m:
            local_names.append(m.group('name'))
            aliases[m.group('name')] = m.group('rhs').strip()
            continue

        m2 = DECL_ONLY_RE.match(ln)
        if m2:
            extracted = _extract_declared_names(m2.group('rest'))
            if extracted:
                local_names.extend(extracted)
                continue

        new_lines.append(ln)
    return '\n'.join(new_lines), local_names, aliases

def _replace_word(text: str, old: str, new: str):
    return re.sub(r'\b' + re.escape(old) + r'\b', new, text)


def _replace_aliases_in_line(line: str, aliases) -> str:
    assign_match = re.match(r'^(?P<indent>\s*)(?P<lhs>[A-Za-z_]\w*)\s*=\s*(?P<rhs>.+)$', line)
    if assign_match:
        rhs = assign_match.group('rhs')
        for old, new in aliases.items():
            rhs = _replace_word(rhs, old, new)
        return f"{assign_match.group('indent')}{assign_match.group('lhs')} = {rhs}"

    if_match = re.match(r'^(?P<indent>\s*)if\s*\(\s*(?P<guard>.+?)\s*\)(?P<suffix>\s*\{?\s*)$', line)
    if if_match:
        guard = if_match.group('guard')
        for old, new in aliases.items():
            guard = _replace_word(guard, old, new)
        return f"{if_match.group('indent')}if ({guard}){if_match.group('suffix')}"

    updated = line
    for old, new in aliases.items():
        updated = _replace_word(updated, old, new)
    return updated


def _fold_guard_temporaries(body: str, aliases) -> str:
    lines = body.splitlines()
    if not lines:
        return body

    output = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = SELF_COMPARE_ASSIGN_RIGHT_RE.match(line) or SELF_COMPARE_ASSIGN_LEFT_RE.match(line)
        if match:
            lhs = match.group('lhs')
            alias_value = aliases.get(lhs)
            if alias_value is not None:
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    if_match = IF_GUARD_RE.match(lines[j])
                    if if_match and if_match.group('guard') == lhs:
                        output.append(
                            f"{if_match.group('indent')}if ({match.group('expr').strip()} {match.group('op')} {alias_value}) {if_match.group('brace')}".rstrip()
                        )
                        i = j + 1
                        continue
        output.append(line)
        i += 1

    return '\n'.join(output)


EVENT_LOCATION_RE = re.compile(
    r'^(?P<indent>\s*)(?P<op>Read|Write)\('
    r'(?P<loc>[A-Za-z_]\w*)'
    r'(?P<rest>,.*)$'
)


def _mark_indirect_register_locations(body: str, local_names) -> str:
    local_set = {name for name in local_names if not is_none_token(name)}
    updated = []

    for line in body.splitlines():
        match = EVENT_LOCATION_RE.match(line)
        if not match:
            updated.append(line)
            continue

        location = match.group('loc')
        if location not in local_set:
            updated.append(line)
            continue

        updated.append(
            f"{match.group('indent')}{match.group('op')}(*{location}{match.group('rest')}"
        )

    return '\n'.join(updated)

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
    aliases_by_tid = {}
    for b in blocks:
        new_body, names, aliases = _remove_local_decls_and_collect(b['body'])
        resolved_aliases = {
            name: _resolve_alias_value(value, aliases)
            for name, value in aliases.items()
        }
        new_body = _fold_guard_temporaries(new_body, resolved_aliases)
        new_body = '\n'.join(
            _replace_aliases_in_line(line, resolved_aliases)
            for line in new_body.splitlines()
        )

        rendered_regs = [
            reg for reg in READ_RENDER_RE.findall(new_body)
            if not is_none_token(reg)
        ]
        locals_by_tid[b['tid']] = [
            name for name in names + rendered_regs
            if not is_none_token(name)
        ]
        new_body = _mark_indirect_register_locations(new_body, locals_by_tid[b['tid']])
        aliases_by_tid[b['tid']] = resolved_aliases
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
                if name in conflicts and not is_none_token(name):
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

        text = _replace_in_exists(text)

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
    e.g. "exists (1:r0=1 /\\ 2:r1=0)" -> "exists (r0=1 /\\ r1=0)"
    """
    updated = []
    in_exists = False
    for ln in text.splitlines():
        stripped = ln.strip().lower()
        if stripped.startswith('exists'):
            in_exists = True
            updated.append(re.sub(r'\b\d+\s*:\s*', '', ln))
            continue
        if in_exists:
            updated.append(re.sub(r'\b\d+\s*:\s*', '', ln))
        else:
            updated.append(ln)
    return '\n'.join(updated)

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
