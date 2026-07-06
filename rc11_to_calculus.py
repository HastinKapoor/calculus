"""
Convert simple rc11-style C thread-litmus tests (in rc11/*.c) into the
calculus_test litmus format.

Usage:
  # Convert single file -> stdout
  python3 rc11_to_calculus.py rc11/2+2W.c

  # Convert single file -> output path
  python3 rc11_to_calculus.py rc11/2+2W.c -o litmus/2+2W.litmus

  # Convert whole dir -> write outputs into litmus/
  python3 rc11_to_calculus.py rc11 -o litmus
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
}


class UnsupportedConstructError(Exception):
    pass

def remove_comments(text: str) -> str:
    # remove C-style block comments and C++ // comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'//.*$', '', text, flags=re.M)
    return text

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
    threads = parse_threads(text)
    converted_threads = []
    global_assign_map = {}  # global var -> qualified reg (e.g., a -> r0_T0)
    unsupported = []
    for tid, (name, body) in enumerate(threads):
        stmts, thread_unsupported = convert_thread_body(body, tid, global_assign_map)
        converted_threads.append((tid, name, stmts))
        unsupported.extend(thread_unsupported)

    if unsupported:
        joined = "\n".join(f"  - {entry}" for entry in unsupported)
        raise UnsupportedConstructError(
            "Unsupported RC11/C constructs encountered during conversion:\n"
            f"{joined}\n"
            "The artifact branch currently supports loads, stores, fences, and simple "
            "register-to-global result assignments. Atomic RMW/CAS operations are not supported."
        )

    # parse final assert and convert to exists
    cond = extract_assert_condition(text)
    exists_line = cond_to_exists(cond, global_assign_map)
    # Build output text
    out_lines = []
    out_lines.append("{}")
    out_lines.append("")  # blank line
    for tid, name, stmts in converted_threads:
        out_lines.append(f"P{tid}(){{")
        for s in stmts:
            out_lines.append(s)
        out_lines.append("}")
        out_lines.append("")  # blank line between threads
    if exists_line:
        out_lines.append(exists_line)
    else:
        out_lines.append("")  # ensure trailing newline
    return "\n".join(out_lines) + "\n"

def process_path(inp: Path, outp: Path):
    text = inp.read_text()
    conv = convert_file_text(text)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(conv)

def main():
    p = argparse.ArgumentParser(description="Convert rc11 C litmus tests to calculus format")
    p.add_argument('input', help='Input file (.c) or directory (rc11/)')
    p.add_argument('-o', '--output', help='Output file or directory (defaults to litmus/ in same workspace)', default=None)
    args = p.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        print(f"Input not found: {inp}")
        return

    failures = []

    if inp.is_dir():
        out_dir = Path(args.output) if args.output else Path.cwd() / 'litmus'
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(inp.glob('*.c')):
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
