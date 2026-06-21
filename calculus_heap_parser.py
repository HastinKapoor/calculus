from __future__ import annotations

import re
from dataclasses import dataclass

from parser_types import Language, MemoryOrder, Operation

try:
    from scripts.linux_to_calculus import convert_and_format
except ImportError:
    convert_and_format = None


@dataclass
class ThreadStatement:
    line: str
    guard_register: "GuardCondition | None" = None


@dataclass(frozen=True)
class GuardCondition:
    register: str
    operator: str = "truthy"
    value: object | None = None


@dataclass
class ParsedLitmus:
    threads: list[list[ThreadStatement]]
    init_statements: list[str]
    constraints: list[dict[str, object]]
    thread_params: list[list[str]]


@dataclass
class ParsedEventSpec:
    location: str | None
    type: Operation
    strength: MemoryOrder
    language: Language
    value: str | None
    register_name: str | None


def single_or_list(specs):
    if len(specs) == 1:
        return specs[0]
    return specs


def is_none_token(value) -> bool:
    return value is None or str(value).strip() == "None"


def normalize_constraint_value(raw):
    raw = raw.strip()
    if is_none_token(raw):
        return None
    if re.fullmatch(r"[+-]?\d+", raw):
        return int(raw)
    return raw


def parse_final_constraint(line):
    line = line.replace("exists", "").strip().strip("()")

    or_clauses = re.split(r"\\/", line)
    final_clauses = []

    for or_clause in or_clauses:
        or_clause = or_clause.strip().strip("()")
        and_clauses = re.split(r"/\\", or_clause)

        condition_set = {}
        for clause in and_clauses:
            if "=" in clause:
                reg, val = clause.split("=")
                normalized_reg = re.sub(r"^\d+\s*:\s*", "", reg.strip())
                condition_set[normalized_reg] = normalize_constraint_value(val)

        if condition_set:
            final_clauses.append(condition_set)

    return final_clauses


def split_args_preserving_nesting(arg_string):
    args = []
    current = []
    depth = 0

    for char in arg_string:
        if char == "," and depth == 0:
            arg = "".join(current).strip()
            if arg:
                args.append(arg)
            current = []
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)

        current.append(char)

    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def parse_thread_params(header_line):
    match = re.match(r"P\d+\s*\((.*)\)\s*\{", header_line)
    if not match:
        return []

    raw_params = match.group(1).strip()
    if not raw_params:
        return []

    params = []
    for raw_param in split_args_preserving_nesting(raw_params):
        names = re.findall(r"[A-Za-z_]\w*", raw_param)
        if names:
            params.append(names[-1])
    return params


def parse_simple_if_guard(line):
    stripped = line.strip()
    if stripped.endswith("{"):
        stripped = stripped[:-1].rstrip()

    match = re.match(r"if\s*\(\s*(.+?)\s*\)\s*$", stripped)
    if not match:
        return None

    expr = match.group(1).strip()
    previous = None
    while expr != previous and expr.startswith("(") and expr.endswith(")"):
        previous = expr
        expr = expr[1:-1].strip()

    comparison = re.fullmatch(
        r"([A-Za-z_]\w*)\s*(==|!=)\s*([A-Za-z_]\w*|[+-]?\d+)",
        expr,
    )
    if comparison:
        raw_value = comparison.group(3)
        value = int(raw_value) if re.fullmatch(r"[+-]?\d+", raw_value) else raw_value
        return GuardCondition(
            register=comparison.group(1),
            operator=comparison.group(2),
            value=value,
        )

    bare = re.fullmatch(r"([A-Za-z_]\w*)", expr)
    if bare:
        return GuardCondition(register=bare.group(1))

    return None


def parse_heap_file(filename):
    threads = []
    thread_params = []
    current_thread = None
    init_statements = []
    constraints = []
    pending_guard = None

    with open(filename, "r") as handle:
        text = handle.read()

    # Source LKMM litmus files often place the opening brace for a thread body
    # on the next line after `P0(...)`. Normalize those files up front so the
    # parser sees the same structure as already-converted inputs.
    if convert_and_format is not None:
        first_nonempty = next((line.strip() for line in text.splitlines() if line.strip()), "")
        has_split_thread_header = bool(
            re.search(r"^\s*(?:P|T)\d+\s*\([^)]*\)\s*$\s*^\s*\{", text, flags=re.M)
        )
        if has_split_thread_header or (
            first_nonempty
            and not first_nonempty.startswith(("{", "P", "T", "exists", "("))
        ):
            text = convert_and_format(text)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("{") and line.endswith("}"):
            init_body = line[1:-1].strip()
            init_statements = [stmt.strip() for stmt in init_body.split(";") if stmt.strip()]
            continue

        if line.startswith("P") and line.endswith("{"):
            current_thread = []
            threads.append(current_thread)
            thread_params.append(parse_thread_params(line))
            continue

        if line == "}":
            current_thread = None
            pending_guard = None
            continue

        if current_thread is not None:
            guard_register = parse_simple_if_guard(line)
            if guard_register is not None:
                pending_guard = guard_register
                continue
            current_thread.append(ThreadStatement(line=line, guard_register=pending_guard))
            pending_guard = None
            continue

        if (line.startswith("(") and line.endswith(")")) or line.startswith("exists"):
            constraints = parse_final_constraint(line)

    return ParsedLitmus(
        threads=threads,
        init_statements=init_statements,
        constraints=constraints,
        thread_params=thread_params,
    )


def parse_operation_call(line):
    match = re.match(r"(\w+)\((.*)\)", line)
    if not match:
        return None, []
    return match.group(1), split_args_preserving_nesting(match.group(2))


def parse_macro_call(line, call_name):
    match = re.match(rf"{re.escape(call_name)}\((.*)\)", line)
    if not match:
        return None
    return split_args_preserving_nesting(match.group(1))


def parse_heap_event_spec(line):
    stripped = line.rstrip(";").strip()

    assignment = re.match(
        r"(?:[A-Za-z_][\w\s\*]*\s+)?([A-Za-z_]\w*)\s*=\s*(.+)",
        stripped,
    )
    if assignment:
        register_name = assignment.group(1)
        rhs = assignment.group(2).strip()

        args = parse_macro_call(rhs, "rcu_dereference")
        if args and len(args) >= 1:
            return ParsedEventSpec(
                location=args[0].strip(),
                type=Operation.READ,
                strength=MemoryOrder.RELAXED,
                language=Language.LINUX,
                value=None,
                register_name=register_name,
            )

        args = parse_macro_call(rhs, "READ_ONCE")
        if args and len(args) >= 1:
            return ParsedEventSpec(
                location=args[0].strip(),
                type=Operation.READ,
                strength=MemoryOrder.RELAXED,
                language=Language.LINUX,
                value=None,
                register_name=register_name,
            )

        args = parse_macro_call(rhs, "smp_load_acquire")
        if args and len(args) >= 1:
            return ParsedEventSpec(
                location=args[0].strip(),
                type=Operation.READ,
                strength=MemoryOrder.ACQUIRE,
                language=Language.LINUX,
                value=None,
                register_name=register_name,
            )

        args = parse_macro_call(rhs, "spin_is_locked")
        if args and len(args) >= 1:
            return ParsedEventSpec(
                location=args[0].strip(),
                type=Operation.READ,
                strength=MemoryOrder.RELAXED,
                language=Language.LINUX,
                value=None,
                register_name=register_name,
            )

    args = parse_macro_call(stripped, "rcu_assign_pointer")
    if args and len(args) >= 2:
        return ParsedEventSpec(
            location=args[0].strip(),
            type=Operation.WRITE,
            strength=MemoryOrder.RELEASE,
            language=Language.LINUX,
            value=args[1].strip(),
            register_name=None,
        )

    args = parse_macro_call(stripped, "WRITE_ONCE")
    if args and len(args) >= 2:
        return ParsedEventSpec(
            location=args[0].strip(),
            type=Operation.WRITE,
            strength=MemoryOrder.RELAXED,
            language=Language.LINUX,
            value=args[1].strip(),
            register_name=None,
        )

    args = parse_macro_call(stripped, "smp_store_release")
    if args and len(args) >= 2:
        return ParsedEventSpec(
            location=args[0].strip(),
            type=Operation.WRITE,
            strength=MemoryOrder.RELEASE,
            language=Language.LINUX,
            value=args[1].strip(),
            register_name=None,
        )

    if parse_macro_call(stripped, "synchronize_rcu") is not None:
        return ParsedEventSpec(None, Operation.FENCE, MemoryOrder.SYNC_RCU, Language.LINUX, None, None)

    if parse_macro_call(stripped, "smp_wmb") is not None:
        return ParsedEventSpec(None, Operation.FENCE, MemoryOrder.WMB, Language.LINUX, None, None)

    if parse_macro_call(stripped, "smp_rmb") is not None:
        return ParsedEventSpec(None, Operation.FENCE, MemoryOrder.RMB, Language.LINUX, None, None)

    if parse_macro_call(stripped, "rcu_read_lock") is not None:
        return ParsedEventSpec(None, Operation.FENCE, MemoryOrder.RCU_LOCK, Language.LINUX, None, None)

    if parse_macro_call(stripped, "rcu_read_unlock") is not None:
        return ParsedEventSpec(None, Operation.FENCE, MemoryOrder.RCU_UNLOCK, Language.LINUX, None, None)

    args = parse_macro_call(stripped, "spin_lock")
    if args and len(args) >= 1:
        return single_or_list([
            ParsedEventSpec(
                location=args[0].strip(),
                type=Operation.READ,
                strength=MemoryOrder.LOCK_READ,
                language=Language.LINUX,
                value="0",
                register_name=None,
            ),
            ParsedEventSpec(
                location=args[0].strip(),
                type=Operation.WRITE,
                strength=MemoryOrder.LOCK_WRITE,
                language=Language.LINUX,
                value="1",
                register_name=None,
            ),
        ])

    args = parse_macro_call(stripped, "spin_unlock")
    if args and len(args) >= 1:
        return ParsedEventSpec(
            location=args[0].strip(),
            type=Operation.WRITE,
            strength=MemoryOrder.UNLOCK,
            language=Language.LINUX,
            value="0",
            register_name=None,
        )

    if parse_macro_call(stripped, "smp_mb__after_spinlock") is not None:
        return ParsedEventSpec(
            None,
            Operation.FENCE,
            MemoryOrder.AFTER_SPINLOCK,
            Language.LINUX,
            None,
            None,
        )

    if parse_macro_call(stripped, "smp_mb__after_unlock_lock") is not None:
        return ParsedEventSpec(
            None,
            Operation.FENCE,
            MemoryOrder.AFTER_UNLOCK_LOCK,
            Language.LINUX,
            None,
            None,
        )

    op_name, args = parse_operation_call(stripped)
    if op_name is None:
        raise ValueError(f"Cannot parse line: {line}")

    try:
        event_type = Operation[op_name.upper()]
    except KeyError as exc:
        raise ValueError(f"{op_name} is not a supported Operation") from exc

    location = args[0].strip() if len(args) >= 1 and not is_none_token(args[0]) else None
    value = args[1].strip() if len(args) >= 2 and not is_none_token(args[1]) else None
    strength = MemoryOrder.RELAXED
    language = Language.C
    register_name = None

    if len(args) >= 3:
        strength = MemoryOrder[args[2].strip().upper()]
    if len(args) >= 4:
        language = Language[args[3].strip().upper()]
    if len(args) >= 5 and not is_none_token(args[4]):
        register_name = args[4].strip()

    return ParsedEventSpec(location, event_type, strength, language, value, register_name)
