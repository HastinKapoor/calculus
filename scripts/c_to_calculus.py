"""
Convert herd-style RC11/C litmus tests into the calculus litmus format.

Usage:
  # Convert single file -> stdout
  python3 scripts/c_to_calculus.py litmus/c/a1+Racq+rel.litmus

  # Convert single file -> output path
  python3 scripts/c_to_calculus.py litmus/c/a1+Racq+rel.litmus -o /tmp/a1+Racq+rel.translated.litmus

  # Convert whole dir -> write outputs into another directory
  python3 scripts/c_to_calculus.py litmus/c -o /tmp/converted-c
"""
import re
import argparse
from pathlib import Path

# Map C11 memory orders to calculus names
ORDER_MAP = {
    'memory_order_relaxed': 'Relaxed',
    'memory_order_seq_cst': 'SEQ_CST',
    'memory_order_acquire': 'Acquire',
    'memory_order_release': 'Release',
    'memory_order_acq_rel': 'ACQ_REL',
    'memory_order_consume': 'Acquire',
}


class UnsupportedConstructError(Exception):
    pass

def remove_comments(text: str) -> str:
    # remove C-style block comments and C++ // comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'//.*$', '', text, flags=re.M)
    return text

def extract_init_block(text: str):
    match = re.search(r'^\s*\{.*\}\s*$', text, re.M)
    return match.group(0).strip() if match else "{}"

def extract_exists_clause(text: str):
    match = re.search(r'^\s*exists\b.*$', text, re.M)
    return match.group(0).strip() if match else None

def parse_litmus_threads(text: str):
    lines = text.splitlines()
    threads = []
    idx = 0

    while idx < len(lines):
        stripped = lines[idx].strip()
        header = re.match(r'^P(?P<tid>\d+)\s*\([^)]*\)\s*\{$', stripped)
        if not header:
            idx += 1
            continue

        tid = int(header.group('tid'))
        idx += 1
        body_lines = []
        depth = 1

        while idx < len(lines):
            line = lines[idx].rstrip()
            line_stripped = line.strip()

            if line_stripped == "}" and depth == 1:
                depth -= 1
                idx += 1
                break

            body_lines.append(line)
            depth += line.count("{") - line.count("}")
            idx += 1

        threads.append((tid, body_lines))

    return threads

def qualify_local_registers(text: str, reg_map: dict[str, str]) -> str:
    for reg, qualified in sorted(reg_map.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(r'\b' + re.escape(reg) + r'\b', qualified, text)
    return text

def normalize_order(raw: str) -> str:
    raw = raw.strip()
    if raw not in ORDER_MAP:
        raise UnsupportedConstructError(f"Unsupported C memory order: {raw}")
    return ORDER_MAP[raw]

def convert_litmus_thread_body(body_lines: list[str], tid: int):
    stmts = []
    unsupported = []
    reg_map = {}
    synthetic_reg_counter = 0

    load_re = re.compile(
        r'^(?:(?:int|bool)\s+)?(?P<reg>\w+)\s*=\s*atomic_load_explicit\s*'
        r'\(\s*&?\s*(?P<loc>\w+)\s*,\s*(?P<ord>[^)]+)\)\s*;\s*$'
    )
    ptr_load_re = re.compile(
        r'^(?:(?:int|bool)\s+)?(?P<reg>\w+)\s*=\s*\*(?P<loc>\w+)\s*;\s*$'
    )
    store_re = re.compile(
        r'^atomic_store_explicit\s*\(\s*&?\s*(?P<loc>\w+)\s*,\s*(?P<val>[^,]+)\s*,\s*(?P<ord>[^)]+)\)\s*;\s*$'
    )
    ptr_store_re = re.compile(r'^\*(?P<loc>\w+)\s*=\s*(?P<val>.+?)\s*;\s*$')
    fence_re = re.compile(r'^atomic_thread_fence\s*\(\s*(?P<ord>[^)]+)\)\s*;\s*$')

    def new_synthetic_reg() -> str:
        nonlocal synthetic_reg_counter
        name = f"tmp_if_{synthetic_reg_counter}"
        synthetic_reg_counter += 1
        qualified = f"{name}_T{tid}"
        reg_map[name] = qualified
        return qualified

    for raw in body_lines:
        line = raw.strip()
        if not line:
            continue

        if line in {"}", "else {", "} else {"}:
            stmts.append(line)
            continue

        if line.startswith("if"):
            condition = re.match(r'^if\s*\((?P<cond>.*)\)\s*(?P<brace>\{?)\s*$', line)
            if not condition:
                unsupported.append(f"T{tid}: {line}")
                continue

            cond = condition.group("cond").strip()
            deref_guard = re.fullmatch(r'\*(?P<loc>\w+)', cond)
            deref_eq_guard = re.fullmatch(r'\*(?P<loc>\w+)\s*(?P<op>==|!=)\s*(?P<rhs>-?\d+)', cond)

            if deref_guard:
                qualified_reg = new_synthetic_reg()
                stmts.append(f"Read({deref_guard.group('loc')}, None, Relaxed, C, {qualified_reg});")
                qualified_cond = qualified_reg
            elif deref_eq_guard:
                qualified_reg = new_synthetic_reg()
                stmts.append(f"Read({deref_eq_guard.group('loc')}, None, Relaxed, C, {qualified_reg});")
                qualified_cond = f"{qualified_reg} {deref_eq_guard.group('op')} {deref_eq_guard.group('rhs')}"
            else:
                if "*" in cond:
                    unsupported.append(f"T{tid}: {line}")
                    continue

                qualified_cond = qualify_local_registers(cond, reg_map)
            brace = " {" if condition.group("brace") else ""
            stmts.append(f"if ({qualified_cond}){brace}")
            continue

        match = load_re.match(line)
        if match:
            reg = match.group("reg")
            reg_map[reg] = f"{reg}_T{tid}"
            stmts.append(
                f"Read({match.group('loc')}, None, {normalize_order(match.group('ord'))}, C, {reg_map[reg]});"
            )
            continue

        match = ptr_load_re.match(line)
        if match:
            reg = match.group("reg")
            reg_map[reg] = f"{reg}_T{tid}"
            stmts.append(f"Read({match.group('loc')}, None, Relaxed, C, {reg_map[reg]});")
            continue

        match = store_re.match(line)
        if match:
            value = qualify_local_registers(match.group("val").strip(), reg_map)
            stmts.append(
                f"Write({match.group('loc')}, {value}, {normalize_order(match.group('ord'))}, C, None);"
            )
            continue

        match = ptr_store_re.match(line)
        if match:
            value = qualify_local_registers(match.group("val").strip(), reg_map)
            stmts.append(f"Write({match.group('loc')}, {value}, Relaxed, C, None);")
            continue

        match = fence_re.match(line)
        if match:
            stmts.append(f"Fence(None, None, {normalize_order(match.group('ord'))}, C, None);")
            continue

        unsupported.append(f"T{tid}: {line}")

    return stmts, reg_map, unsupported

def rewrite_exists_clause(exists_line: str, thread_reg_maps: dict[int, dict[str, str]]) -> str:
    if not exists_line:
        return ""

    def replace_thread_reg(match):
        tid = int(match.group("tid"))
        reg = match.group("reg")
        return thread_reg_maps.get(tid, {}).get(reg, reg)

    return re.sub(
        r'(?P<tid>\d+):(?P<reg>[A-Za-z_]\w*)',
        replace_thread_reg,
        exists_line,
    )

def convert_litmus_text(text: str):
    threads = parse_litmus_threads(text)
    if not threads:
        raise UnsupportedConstructError(
            "No RC11/C litmus threads were found. Expected herd-style `.litmus` input with `P0 (...) { ... }` blocks."
        )

    out_lines = [extract_init_block(text), ""]
    thread_reg_maps = {}
    unsupported = []

    for tid, body_lines in threads:
        stmts, reg_map, thread_unsupported = convert_litmus_thread_body(body_lines, tid)
        thread_reg_maps[tid] = reg_map
        unsupported.extend(thread_unsupported)

        out_lines.append(f"P{tid}(){{")
        out_lines.extend(stmts)
        out_lines.append("}")
        out_lines.append("")

    if unsupported:
        joined = "\n".join(f"  - {entry}" for entry in unsupported)
        raise UnsupportedConstructError(
            "Unsupported RC11/C `.litmus` constructs encountered during conversion:\n"
            f"{joined}\n"
            "The artifact branch currently supports loads, stores, fences, and control flow over translated registers. "
            "Atomic CAS/RMW and arithmetic-over-read expressions are not yet supported."
        )

    exists_line = rewrite_exists_clause(extract_exists_clause(text), thread_reg_maps)
    if exists_line:
        out_lines.append(exists_line)

    return "\n".join(out_lines) + "\n"

def _normalize_location(loc: str) -> str:
    # accept forms like &x, *x, (*x) and return plain x
    if loc is None:
        return loc
    s = loc.strip()
    # strip leading & or single *
    s = re.sub(r'^[&\*]\s*', '', s)
    # unwrap parentheses
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1].strip()
    return s

def parse_threads(text: str):
    """
    Find thread functions of form:
      void *thread_name(void *arg) { ... }
    Return list of (name, body) in occurrence order.
    """
    pattern = re.compile(r'void\s*\*\s*(?P<name>\w+)\s*\(\s*void\s*\*\w*\s*\)\s*\{(?P<body>.*?)\}', re.S)
    threads = []
    for m in pattern.finditer(text):
        threads.append((m.group('name'), m.group('body')))
    return threads

def extract_assert_condition(text: str):
    """
    Try to find a final assertion of the form:
      assert(!( ... ));
    Return the inner condition as a string, or None.
    """
    m = re.search(r'assert\s*\(\s*!\s*\(\s*(?P<cond>.*?)\s*\)\s*\)\s*;', text, re.S)
    if m:
        return m.group('cond').strip()
    # fallback: assert(condition);
    m2 = re.search(r'assert\s*\(\s*(?P<cond>.*?)\s*\)\s*;', text, re.S)
    if m2:
        return m2.group('cond').strip()
    return None

def cond_to_exists(cond: str, var_map: dict):
    """
    Convert a C boolean condition into the calculus "exists" clause.
    - Replace && -> /\
    - Replace == -> =
    - Replace variable names that have a mapping in var_map
    """
    if cond is None:
        return None
    s = cond
    s = s.replace('&&', '/\\')
    s = s.replace('||', '\\/')
    s = s.replace('==', '=')
    # normalize whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    # replace occurrences of variable names by mapped register names if provided
    # match word boundaries for variable names
    for var, reg in var_map.items():
        s = re.sub(r'\b' + re.escape(var) + r'\b', reg, s)
    return f"exists ({s})"

def convert_thread_body(body: str, tid: int, global_assign_map: dict):
    """
    Convert a thread body to a list of calculus statements.
    Record mapping from global variables assigned from registers to register names in global_assign_map.
    """
    stmts = []
    unsupported = []
    # split on semicolons, keep ordering
    parts = [p.strip() for p in body.split(';')]
    # keep a mapping of original register names in this thread -> qualified reg name
    reg_qual = {}
    for part in parts:
        if not part:
            continue

        # ignore return statements entirely
        if re.match(r'^\s*return\b', part):
            continue

        # detect atomic_store_explicit(x, val, memory_order_...)  OR atomic_store_explicit(&x, val, ...)
        m_store = re.search(r'atomic_store_explicit\s*\(\s*&?\s*(?P<loc>\w+)\s*,\s*(?P<val>[^,()]+)\s*,\s*(?P<ord>[^)]+)\)', part)
        if m_store:
            loc = _normalize_location(m_store.group('loc'))
            val = m_store.group('val').strip()
            ordraw = m_store.group('ord').strip()
            order = ORDER_MAP.get(ordraw, 'Relaxed')
            stmts.append(f"Write({loc}, {val}, {order}, C, None);")
            continue

        # detect plain pointer store: *a = expr  -> Write(a, expr, SEQ_CST, C, None)
        m_ptr_store = re.match(r'^\s*\*\s*(?P<loc>[A-Za-z_]\w*)\s*=\s*(?P<val>.+)$', part)
        if m_ptr_store:
            loc = _normalize_location(m_ptr_store.group('loc'))
            val = m_ptr_store.group('val').strip()
            stmts.append(f"Write({loc}, {val}, SEQ_CST, C, None);")
            continue

        # detect rX = atomic_load_explicit(x, memory_order_...) OR with &x
        m_load = re.search(r'(?P<reg>\w+)\s*=\s*atomic_load_explicit\s*\(\s*&?\s*(?P<loc>\w+)\s*,\s*(?P<ord>[^)]+)\)', part)
        if m_load:
            reg = m_load.group('reg')
            loc = _normalize_location(m_load.group('loc'))
            ordraw = m_load.group('ord').strip()
            order = ORDER_MAP.get(ordraw, 'Relaxed')
            qual_reg = f"{reg}_T{tid}"
            reg_qual[reg] = qual_reg
            stmts.append(f"Read({loc}, None, {order}, C, {qual_reg});")
            continue

        # detect atomic_thread_fence(memory_order_...)
        m_fence = re.search(r'atomic_thread_fence\s*\(\s*(?P<ord>[^)]+)\s*\)', part)
        if m_fence:
            ordraw = m_fence.group('ord').strip()
            order = ORDER_MAP.get(ordraw, 'SEQ_CST')
            # map seq_cst -> SEQ_CST, others default
            stmts.append(f"Fence(None, None, {order}, C, None);")
            continue

        # detect simple assignment like a = r0
        m_assign = re.search(r'(?P<lhs>\w+)\s*=\s*(?P<rhs>\w+)\s*$', part)
        if m_assign:
            lhs = m_assign.group('lhs')
            rhs = m_assign.group('rhs')
            # if rhs is a register read we converted, map global variable to that qualified reg
            if rhs in reg_qual:
                global_assign_map[lhs] = reg_qual[rhs]
                # omit emitting this assignment as an event (we record mapping for exists)
                continue
            else:
                # emit a write with unknown value (conservative: use SEQ_CST as plain stores default)
                stmts.append(f"Write({lhs}, None, SEQ_CST, C, None);")
                continue

        # Surface unsupported constructs instead of emitting malformed litmus.
        cleaned = part.replace('\n', ' ').strip()
        if cleaned:
            unsupported.append(f"T{tid}: {cleaned}")
    return stmts, unsupported

def convert_file_text(text: str):
    text = remove_comments(text)
    return convert_litmus_text(text)

def process_path(inp: Path, outp: Path):
    text = inp.read_text()
    conv = convert_file_text(text)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(conv)

def main():
    p = argparse.ArgumentParser(description="Convert RC11/C .litmus tests to calculus format")
    p.add_argument('input', help='Input .litmus file or directory (for example litmus/c/)')
    p.add_argument('-o', '--output', help='Output file or directory', default=None)
    args = p.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        print(f"Input not found: {inp}")
        return

    failures = []

    if inp.is_dir():
        out_dir = Path(args.output) if args.output else Path.cwd() / 'litmus'
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(inp.glob('*.litmus')):
            out_path = out_dir / (f.stem + '.litmus')
            try:
                process_path(f, out_path)
            except UnsupportedConstructError as exc:
                failures.append((f, str(exc)))
                continue
            print(f"Wrote {out_path}")
    else:
        outp = Path(args.output) if args.output else None
        try:
            converted = convert_file_text(inp.read_text())
            if outp:
                outp.parent.mkdir(parents=True, exist_ok=True)
                outp.write_text(converted)
                print(f"Wrote {outp}")
            else:
                print(converted)
        except UnsupportedConstructError as exc:
            failures.append((inp, str(exc)))

    if failures:
        for path, message in failures:
            print(f"Conversion failed for {path}:", flush=True)
            print(message, flush=True)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
