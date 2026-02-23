#!/usr/bin/env python3

"""Convert C11/LKMM litmus tests into the calculus litmus format."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


C11_DIR = "C11"
LKMM_DIR = "LKMM"

CONTROL_FLOW_RE = re.compile(r"^(if|else|while|for|switch)\b")
THREAD_HEADER_RE = re.compile(r"^P(\d+)\s*\((.*)\)\s*(\{)?\s*$")
SIMPLE_ASSIGN_RE = re.compile(r"^(?:[A-Za-z_][\w\s\*\(\)]*\s+)?([A-Za-z_]\w*)\s*=\s*(.+?)\s*;?$")
NUMERIC_RE = re.compile(r"^[+-]?\d+$")

TYPE_KEYWORDS = {
    "atomic_int",
    "int",
    "intptr_t",
    "long",
    "short",
    "volatile",
    "const",
    "unsigned",
    "signed",
    "struct",
    "union",
    "char",
    "void",
}

C11_MO_MAP = {
    "memory_order_relaxed": "Relaxed",
    "memory_order_acquire": "Acquire",
    "memory_order_release": "Release",
    "memory_order_acq_rel": "Acq_rel",
    "memory_order_seq_cst": "SEQ_CST",
}

REASON_TO_FEATURE = {
    "contains control flow (if/loop/switch)": "Control flow support in threads (`if`/`else`/`while`/`for`/`switch`)",
    "contains unsupported CAS/RMW": "Atomic RMW/CAS operations",
    "contains unsupported computed assignment": "Computed register/value expressions",
    "contains unsupported statement": "Additional C/LKMM statement forms and expression patterns",
    "missing/empty final constraint": "Robust parsing for missing/non-standard `exists` constraints",
}

FEATURE_ORDER = [
    "Control flow support in threads (`if`/`else`/`while`/`for`/`switch`)",
    "Atomic RMW/CAS operations",
    "Computed register/value expressions",
    "Additional C/LKMM statement forms and expression patterns",
    "Robust parsing for missing/non-standard `exists` constraints",
]


@dataclass
class Operation:
    op: str
    location: str
    value: str
    memory_order: str
    language: str
    register: str

    def render(self) -> str:
        return (
            f"{self.op}("
            f"{self.location}, {self.value}, {self.memory_order}, {self.language}, {self.register}"
            ");"
        )


@dataclass
class ThreadState:
    thread_id: int
    params: List[str]
    ops: List[Operation] = field(default_factory=list)
    aliases: Dict[str, str] = field(default_factory=dict)
    reg_map: Dict[str, str] = field(default_factory=dict)
    diagnostics: List["Diagnostic"] = field(default_factory=list)
    skip_block_depth: int = 0
    skip_single_statement: bool = False
    incomplete_reasons: Set[str] = field(default_factory=set)


@dataclass
class ConversionResult:
    output_text: str
    incomplete: bool
    reasons: Set[str]
    diagnostics: List["Diagnostic"]


@dataclass
class Diagnostic:
    line_no: int
    line_text: str
    reason: str
    unsupported_op: str


def reasons_to_features(reasons: Set[str]) -> List[str]:
    features = {REASON_TO_FEATURE.get(reason, reason) for reason in reasons}
    return sorted(features, key=lambda f: (FEATURE_ORDER.index(f) if f in FEATURE_ORDER else 999, f))


def extract_unsupported_operation(line: str) -> str:
    stripped = line.strip()
    ctrl = re.match(r"^(if|else|while|for|switch)\b", stripped)
    if ctrl:
        return ctrl.group(1)

    call = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
    if call:
        return call.group(1)

    assign = re.match(r".*?=\s*(.+?);?\s*$", stripped)
    if assign:
        return assign.group(1).strip()
    return stripped


def record_issue(thread: ThreadState, reason: str, line_no: int, line_text: str) -> None:
    thread.incomplete_reasons.add(reason)
    thread.diagnostics.append(
        Diagnostic(
            line_no=line_no,
            line_text=line_text.rstrip("\n"),
            reason=reason,
            unsupported_op=extract_unsupported_operation(line_text),
        )
    )


def strip_inline_comment(line: str) -> str:
    if "//" in line:
        return line.split("//", 1)[0]
    return line


def split_args(args_str: str) -> List[str]:
    args: List[str] = []
    current: List[str] = []
    depth = 0
    for ch in args_str:
        if ch == "," and depth == 0:
            arg = "".join(current).strip()
            if arg:
                args.append(arg)
            current = []
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def parse_call(expr: str, call_name: str) -> Optional[List[str]]:
    pattern = rf"^\s*{re.escape(call_name)}\((.*)\)\s*;?\s*$"
    match = re.match(pattern, expr)
    if not match:
        return None
    return split_args(match.group(1))


def normalize_identifier(expr: str) -> str:
    expr = expr.strip()
    if NUMERIC_RE.match(expr):
        return expr

    ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)
    filtered = [tok for tok in ids if tok not in TYPE_KEYWORDS]
    if filtered:
        return filtered[-1]
    return expr.strip()


def normalize_location(expr: str) -> str:
    cleaned = expr.strip()
    cleaned = re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", cleaned)
    cleaned = cleaned.lstrip("&")
    cleaned = cleaned.lstrip("*").strip()
    cleaned = cleaned.strip("()")
    return normalize_identifier(cleaned)


def resolve_alias(value: str, aliases: Dict[str, str], max_hops: int = 6) -> str:
    current = value.strip()
    for _ in range(max_hops):
        if current in aliases:
            nxt = aliases[current].strip()
            if nxt == current:
                break
            current = nxt
            continue
        break
    return current


def normalize_value(expr: str, aliases: Dict[str, str]) -> str:
    value = expr.strip()
    value = resolve_alias(value, aliases)
    value = value.strip()

    if value in {"true", "false"}:
        return "1" if value == "true" else "0"
    if NUMERIC_RE.match(value):
        return value

    # Remove simple casts around scalar values.
    value = re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", value).strip()
    value = resolve_alias(value, aliases)
    if NUMERIC_RE.match(value):
        return value
    return normalize_identifier(value)


def parse_c11_memory_order(raw: str) -> str:
    return C11_MO_MAP.get(raw.strip(), "Relaxed")


def parse_thread_params(raw: str) -> List[str]:
    raw = raw.strip()
    if not raw:
        return []
    params = split_args(raw)
    result: List[str] = []
    for param in params:
        token = normalize_identifier(param)
        if token:
            result.append(token)
    return result


def is_simple_alias_expr(expr: str) -> bool:
    expr = expr.strip()
    if NUMERIC_RE.match(expr):
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr))


def make_register_name(counter: int) -> str:
    return f"r{counter}"


def convert_constraint_text(
    raw_constraint: str, thread_reg_maps: Dict[int, Dict[str, str]]
) -> str:
    if not raw_constraint.strip():
        return ""

    text = " ".join(raw_constraint.split())
    if text.startswith("exists"):
        text = text[len("exists") :].strip()
    if not (text.startswith("(") and text.endswith(")")):
        first = text.find("(")
        last = text.rfind(")")
        if first != -1 and last != -1 and first < last:
            text = text[first : last + 1]
        else:
            text = f"({text})"

    expr = text.strip()[1:-1].strip()

    def replace_thread_prefixed(match: re.Match[str]) -> str:
        thread_id = int(match.group(1))
        src_reg = match.group(2)
        mapped = thread_reg_maps.get(thread_id, {}).get(src_reg)
        return mapped if mapped else match.group(0)

    expr = re.sub(r"\b(\d+):([A-Za-z_][A-Za-z0-9_]*)\b", replace_thread_prefixed, expr)

    # For unprefixed register constraints, rewrite only when the source name is unique.
    reverse_map: Dict[str, Set[str]] = {}
    for reg_map in thread_reg_maps.values():
        for src_name, out_name in reg_map.items():
            reverse_map.setdefault(src_name, set()).add(out_name)

    def replace_unprefixed_lhs(match: re.Match[str]) -> str:
        lhs = match.group(1)
        outs = reverse_map.get(lhs)
        if outs and len(outs) == 1:
            return f"{next(iter(outs))}="
        return match.group(0)

    expr = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=", replace_unprefixed_lhs, expr)
    return f"exists ({expr})"


def parse_init_assignments(lines: List[str]) -> List[Tuple[str, str]]:
    joined = " ".join(lines)
    assignments: List[Tuple[str, str]] = []
    for part in joined.split(";"):
        stmt = part.strip()
        if not stmt or "=" not in stmt:
            continue
        lhs, rhs = stmt.split("=", 1)
        lhs = lhs.strip()
        rhs = rhs.strip()
        if lhs.startswith("[") and lhs.endswith("]"):
            loc = lhs
        else:
            loc = f"[{normalize_location(lhs)}]"
        assignments.append((loc, rhs))
    return assignments


def handle_control_flow(thread: ThreadState, line_no: int, line: str) -> bool:
    stripped = line.strip()
    lead = stripped.lstrip("}").strip()
    if not lead:
        return False
    if not CONTROL_FLOW_RE.match(lead):
        return False

    record_issue(thread, "contains control flow (if/loop/switch)", line_no, line)

    brace_delta = line.count("{") - line.count("}")
    if "{" in line:
        thread.skip_block_depth += max(1, brace_delta if brace_delta > 0 else 1)
    else:
        thread.skip_single_statement = True
    return True


def parse_assignment(
    language: str,
    statement: str,
    thread: ThreadState,
    next_reg_counter: int,
    line_no: int,
    line_text: str,
) -> Tuple[Optional[Operation], int]:
    assign = SIMPLE_ASSIGN_RE.match(statement)
    if not assign:
        return None, next_reg_counter

    lhs = assign.group(1)
    rhs = assign.group(2).strip()

    if rhs.startswith("atomic_load_explicit("):
        args = parse_call(rhs, "atomic_load_explicit")
        if args and len(args) >= 2:
            reg = thread.reg_map.get(lhs)
            if not reg:
                reg = make_register_name(next_reg_counter)
                next_reg_counter += 1
                thread.reg_map[lhs] = reg
            op = Operation(
                op="Read",
                location=normalize_location(args[0]),
                value="None",
                memory_order=parse_c11_memory_order(args[1]),
                language=language,
                register=reg,
            )
            return op, next_reg_counter

    if rhs.startswith("READ_ONCE("):
        args = parse_call(rhs, "READ_ONCE")
        if args and len(args) >= 1:
            reg = thread.reg_map.get(lhs)
            if not reg:
                reg = make_register_name(next_reg_counter)
                next_reg_counter += 1
                thread.reg_map[lhs] = reg
            op = Operation(
                op="Read",
                location=normalize_location(args[0]),
                value="None",
                memory_order="Relaxed",
                language=language,
                register=reg,
            )
            return op, next_reg_counter

    if rhs.startswith("smp_load_acquire("):
        args = parse_call(rhs, "smp_load_acquire")
        if args and len(args) >= 1:
            reg = thread.reg_map.get(lhs)
            if not reg:
                reg = make_register_name(next_reg_counter)
                next_reg_counter += 1
                thread.reg_map[lhs] = reg
            op = Operation(
                op="Read",
                location=normalize_location(args[0]),
                value="None",
                memory_order="Acquire",
                language=language,
                register=reg,
            )
            return op, next_reg_counter

    if "rcu_dereference(" in rhs:
        rcu_match = re.search(r"rcu_dereference\((.*)\)", rhs)
        if rcu_match:
            reg = thread.reg_map.get(lhs)
            if not reg:
                reg = make_register_name(next_reg_counter)
                next_reg_counter += 1
                thread.reg_map[lhs] = reg
            op = Operation(
                op="Read",
                location=normalize_location(rcu_match.group(1)),
                value="None",
                memory_order="Acquire",
                language=language,
                register=reg,
            )
            return op, next_reg_counter

    if rhs.startswith("atomic_compare_exchange_strong_explicit("):
        args = parse_call(rhs, "atomic_compare_exchange_strong_explicit")
        record_issue(thread, "contains unsupported CAS/RMW", line_no, line_text)
        if args and len(args) >= 4:
            reg = thread.reg_map.get(lhs)
            if not reg:
                reg = make_register_name(next_reg_counter)
                next_reg_counter += 1
                thread.reg_map[lhs] = reg
            op = Operation(
                op="Read",
                location=normalize_location(args[0]),
                value="None",
                memory_order=parse_c11_memory_order(args[3]),
                language=language,
                register=reg,
            )
            return op, next_reg_counter

    # Plain dereference reads (non-atomic or through casted pointers).
    if rhs.startswith("*") or re.match(r"^\(\s*[^()]*\)\s*\*", rhs):
        reg = thread.reg_map.get(lhs)
        if not reg:
            reg = make_register_name(next_reg_counter)
            next_reg_counter += 1
            thread.reg_map[lhs] = reg
        op = Operation(
            op="Read",
            location=normalize_location(rhs),
            value="None",
            memory_order="Relaxed",
            language=language,
            register=reg,
        )
        return op, next_reg_counter

    # Track simple aliases/constants for write value resolution.
    rhs_no_cast = re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", rhs).strip()
    if is_simple_alias_expr(rhs_no_cast):
        thread.aliases[lhs] = rhs_no_cast
        return None, next_reg_counter

    if lhs in thread.reg_map:
        # Keep register assignment unknown to help report incompleteness.
        record_issue(thread, "contains unsupported computed assignment", line_no, line_text)
        return None, next_reg_counter

    record_issue(thread, "contains unsupported statement", line_no, line_text)
    return None, next_reg_counter


def parse_non_assignment(
    language: str, statement: str, thread: ThreadState
) -> Tuple[bool, Optional[Operation]]:
    stmt = statement.strip()

    args = parse_call(stmt, "atomic_store_explicit")
    if args and len(args) >= 3:
        return True, Operation(
            op="Write",
            location=normalize_location(args[0]),
            value=normalize_value(args[1], thread.aliases),
            memory_order=parse_c11_memory_order(args[2]),
            language=language,
            register="None",
        )

    args = parse_call(stmt, "WRITE_ONCE")
    if args and len(args) >= 2:
        return True, Operation(
            op="Write",
            location=normalize_location(args[0]),
            value=normalize_value(args[1], thread.aliases),
            memory_order="Relaxed",
            language=language,
            register="None",
        )

    args = parse_call(stmt, "smp_store_release")
    if args and len(args) >= 2:
        return True, Operation(
            op="Write",
            location=normalize_location(args[0]),
            value=normalize_value(args[1], thread.aliases),
            memory_order="Release",
            language=language,
            register="None",
        )

    args = parse_call(stmt, "rcu_assign_pointer")
    if args and len(args) >= 2:
        return True, Operation(
            op="Write",
            location=normalize_location(args[0]),
            value=normalize_value(args[1], thread.aliases),
            memory_order="Release",
            language=language,
            register="None",
        )

    if parse_call(stmt, "smp_mb") is not None:
        return True, Operation(
            op="Fence",
            location="None",
            value="None",
            memory_order="SEQ_CST",
            language=language,
            register="None",
        )

    if parse_call(stmt, "smp_rmb") is not None:
        return True, Operation(
            op="Fence",
            location="None",
            value="None",
            memory_order="SEQ_CST",
            language=language,
            register="None",
        )

    if parse_call(stmt, "synchronize_rcu") is not None:
        return True, Operation(
            op="Fence",
            location="None",
            value="None",
            memory_order="SEQ_CST",
            language=language,
            register="None",
        )

    if parse_call(stmt, "rcu_read_lock") is not None:
        return True, None

    if parse_call(stmt, "rcu_read_unlock") is not None:
        return True, None

    # Plain non-atomic write through pointer.
    plain_store = re.match(r"^\s*\*\s*(.+?)\s*=\s*(.+?)\s*;?\s*$", stmt)
    if plain_store:
        return True, Operation(
            op="Write",
            location=normalize_location(plain_store.group(1)),
            value=normalize_value(plain_store.group(2), thread.aliases),
            memory_order="Relaxed",
            language=language,
            register="None",
        )

    return False, None


def process_thread_line(
    line_no: int, line: str, language: str, thread: ThreadState, next_reg_counter: int
) -> int:
    statement = strip_inline_comment(line).strip()
    if not statement:
        return next_reg_counter

    if thread.skip_block_depth > 0:
        thread.skip_block_depth += line.count("{") - line.count("}")
        if thread.skip_block_depth <= 0:
            thread.skip_block_depth = 0
        return next_reg_counter

    if thread.skip_single_statement:
        if "{" in line:
            thread.skip_block_depth += max(1, line.count("{") - line.count("}"))
        if ";" in line and thread.skip_block_depth == 0:
            thread.skip_single_statement = False
        return next_reg_counter

    if handle_control_flow(thread, line_no, line):
        return next_reg_counter

    clean_stmt = statement.strip("{} ").strip()
    if not clean_stmt:
        return next_reg_counter

    op, next_reg_counter = parse_assignment(
        language, clean_stmt, thread, next_reg_counter, line_no, line
    )
    if op is not None:
        thread.ops.append(op)
        return next_reg_counter

    handled, op = parse_non_assignment(language, clean_stmt, thread)
    if op is not None:
        thread.ops.append(op)
        return next_reg_counter
    if handled:
        return next_reg_counter

    # Harmless declaration-style aliases (e.g., intptr_t r3=x2;).
    assign = SIMPLE_ASSIGN_RE.match(clean_stmt)
    if assign:
        lhs = assign.group(1)
        rhs = assign.group(2).strip()
        rhs_no_cast = re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", rhs).strip()
        if is_simple_alias_expr(rhs_no_cast):
            thread.aliases[lhs] = rhs_no_cast
            return next_reg_counter

    record_issue(thread, "contains unsupported statement", line_no, line)
    return next_reg_counter


def convert_file(path: Path, language: str) -> ConversionResult:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    init_block_lines: List[str] = []
    collecting_init = False
    init_depth = 0
    seen_first_thread = False

    current_thread: Optional[ThreadState] = None
    current_thread_depth = 0
    waiting_for_thread_open = False
    threads: List[ThreadState] = []

    collecting_constraint = False
    constraint_lines: List[str] = []
    constraint_balance = 0

    global_reg_counter = 0
    file_reasons: Set[str] = set()
    file_diagnostics: List[Diagnostic] = []

    for line_no, raw_line in enumerate(lines, start=1):
        line_no_comment = strip_inline_comment(raw_line).rstrip()
        stripped = line_no_comment.strip()

        if collecting_constraint:
            if stripped:
                constraint_lines.append(stripped)
            constraint_balance += stripped.count("(") - stripped.count(")")
            if constraint_balance <= 0 and any("(" in s for s in constraint_lines):
                collecting_constraint = False
            continue

        if current_thread is not None:
            if waiting_for_thread_open:
                if "{" in line_no_comment:
                    current_thread_depth += line_no_comment.count("{") - line_no_comment.count("}")
                    waiting_for_thread_open = False
                continue

            global_reg_counter = process_thread_line(
                line_no, raw_line, language, current_thread, global_reg_counter
            )

            current_thread_depth += line_no_comment.count("{") - line_no_comment.count("}")
            if current_thread_depth <= 0:
                file_reasons.update(current_thread.incomplete_reasons)
                file_diagnostics.extend(current_thread.diagnostics)
                threads.append(current_thread)
                current_thread = None
                current_thread_depth = 0
                waiting_for_thread_open = False
            continue

        if stripped.startswith("exists"):
            collecting_constraint = True
            if stripped:
                constraint_lines.append(stripped)
            constraint_balance = stripped.count("(") - stripped.count(")")
            if constraint_balance <= 0 and "(" in stripped and ")" in stripped:
                collecting_constraint = False
            continue

        if not seen_first_thread and stripped.startswith("{"):
            collecting_init = True
            init_depth += stripped.count("{") - stripped.count("}")
            init_block_lines.append(stripped.replace("{", "").replace("}", " "))
            if init_depth <= 0:
                collecting_init = False
                init_depth = 0
            continue

        if collecting_init:
            init_block_lines.append(stripped.replace("{", "").replace("}", " "))
            init_depth += stripped.count("{") - stripped.count("}")
            if init_depth <= 0:
                collecting_init = False
                init_depth = 0
            continue

        header = THREAD_HEADER_RE.match(stripped)
        if header:
            seen_first_thread = True
            tid = int(header.group(1))
            params = parse_thread_params(header.group(2))
            current_thread = ThreadState(thread_id=tid, params=params)
            if header.group(3):
                current_thread_depth = 1
                waiting_for_thread_open = False
            else:
                current_thread_depth = 0
                waiting_for_thread_open = True
            continue

    if current_thread is not None:
        file_reasons.update(current_thread.incomplete_reasons)
        file_diagnostics.extend(current_thread.diagnostics)
        threads.append(current_thread)

    init_assignments = parse_init_assignments(init_block_lines)
    thread_reg_maps = {thread.thread_id: thread.reg_map for thread in threads}
    raw_constraint = " ".join(constraint_lines)
    converted_constraint = convert_constraint_text(raw_constraint, thread_reg_maps)

    output_lines: List[str] = []
    if init_assignments:
        init_str = " ".join(f"{lhs} = {rhs};" for lhs, rhs in init_assignments)
        output_lines.append(f"{{ {init_str} }}")
        output_lines.append("")

    for thread in sorted(threads, key=lambda t: t.thread_id):
        output_lines.append(f"P{thread.thread_id}({', '.join(thread.params)}) {{")
        for op in thread.ops:
            output_lines.append(f"\t{op.render()}")
        output_lines.append("}")
        output_lines.append("")

    if converted_constraint:
        output_lines.append(converted_constraint)
    else:
        output_lines.append("exists ()")
        file_reasons.add("missing/empty final constraint")

    # Remove trailing blank lines before constraint if present.
    text = "\n".join(output_lines).rstrip() + "\n"
    return ConversionResult(
        output_text=text,
        incomplete=bool(file_reasons),
        reasons=file_reasons,
        diagnostics=file_diagnostics,
    )


def convert_all(source_root: Path, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    report_entries: List[Tuple[str, List[str], List[str]]] = []

    for subdir_name, language in ((C11_DIR, "C"), (LKMM_DIR, "Linux")):
        src_dir = source_root / subdir_name
        dst_dir = output_root / subdir_name
        dst_dir.mkdir(parents=True, exist_ok=True)
        for old_file in dst_dir.rglob("*.litmus"):
            old_file.unlink()

        for src_file in sorted(src_dir.rglob("*.litmus")):
            rel = src_file.relative_to(src_dir)
            stem = src_file.stem
            result = convert_file(src_file, language)

            suffix = "_calculus_incomplete.litmus" if result.incomplete else "_calculus.litmus"
            out_file = (dst_dir / rel).with_name(stem + suffix)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(result.output_text, encoding="utf-8")

            if result.incomplete:
                features = reasons_to_features(result.reasons)
                report_entries.append(
                    (f"{subdir_name}/{rel.name}", sorted(result.reasons), features)
                )

    report_path = output_root / "CONVERSION_REPORT.md"
    lines: List[str] = ["# Conversion Report", ""]
    if not report_entries:
        lines.append("All files converted without unsupported constructs.")
    else:
        needed_features = sorted(
            {feature for _, _, features in report_entries for feature in features},
            key=lambda f: (FEATURE_ORDER.index(f) if f in FEATURE_ORDER else 999, f),
        )

        lines.append("## Calculus Feature To-Do")
        lines.append("")
        for feature in needed_features:
            lines.append(f"- [ ] {feature}")
        lines.append("")

        lines.append("## Incomplete Litmus Tests")
        lines.append("")
        lines.append("Each entry lists the missing feature(s) needed for full conversion.")
        lines.append("")
        for filename, _reasons, features in report_entries:
            lines.append(f"- `{filename}`: {', '.join(features)}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def infer_single_file_context(
    input_file: Path, source_root: Path
) -> Tuple[Optional[str], Optional[str], Optional[Path]]:
    lang: Optional[str] = None
    subdir: Optional[str] = None
    rel_under_subdir: Optional[Path] = None

    try:
        rel = input_file.resolve().relative_to(source_root.resolve())
        if rel.parts:
            if rel.parts[0] == C11_DIR:
                lang = "C"
                subdir = C11_DIR
                rel_under_subdir = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(input_file.name)
            elif rel.parts[0] == LKMM_DIR:
                lang = "Linux"
                subdir = LKMM_DIR
                rel_under_subdir = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(input_file.name)
    except Exception:
        pass

    if lang is None:
        parts = set(input_file.parts)
        if C11_DIR in parts:
            lang = "C"
        elif LKMM_DIR in parts:
            lang = "Linux"

    return lang, subdir, rel_under_subdir


def write_single_file_output(
    result: ConversionResult,
    input_file: Path,
    output_root: Path,
    inferred_subdir: Optional[str],
    rel_under_subdir: Optional[Path],
) -> Path:
    suffix = "_calculus_incomplete.litmus" if result.incomplete else "_calculus.litmus"

    if inferred_subdir and rel_under_subdir:
        out_rel = rel_under_subdir.with_name(rel_under_subdir.stem + suffix)
        out_file = output_root / inferred_subdir / out_rel
    else:
        out_file = output_root / f"{input_file.stem}{suffix}"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(result.output_text, encoding="utf-8")
    return out_file


def report_single_file_errors(input_file: Path, result: ConversionResult) -> None:
    print(
        f"Unsupported operations encountered while converting {input_file}:",
        file=sys.stderr,
    )

    if result.diagnostics:
        seen = set()
        ordered = sorted(result.diagnostics, key=lambda d: (d.line_no, d.line_text, d.reason))
        for diag in ordered:
            key = (diag.line_no, diag.line_text, diag.reason, diag.unsupported_op)
            if key in seen:
                continue
            seen.add(key)
            feature = REASON_TO_FEATURE.get(diag.reason, diag.reason)
            print(f"- line {diag.line_no}: {diag.line_text}", file=sys.stderr)
            print(f"  unsupported operation: {diag.unsupported_op}", file=sys.stderr)
            print(f"  missing feature: {feature}", file=sys.stderr)
    else:
        for reason in sorted(result.reasons):
            feature = REASON_TO_FEATURE.get(reason, reason)
            print(f"- {reason} (feature: {feature})", file=sys.stderr)


def convert_single(
    input_file: Path,
    source_root: Path,
    output_root: Path,
    language: Optional[str],
) -> int:
    inferred_lang, subdir, rel_under_subdir = infer_single_file_context(input_file, source_root)
    final_lang = language or inferred_lang
    if final_lang is None:
        print(
            "Could not infer language for input file. Use --language C or --language Linux.",
            file=sys.stderr,
        )
        return 2

    result = convert_file(input_file, final_lang)
    out_file = write_single_file_output(result, input_file, output_root, subdir, rel_under_subdir)

    if result.incomplete:
        report_single_file_errors(input_file, result)
        print(f"Partial output written to: {out_file}", file=sys.stderr)
        return 2

    print(f"Converted successfully: {out_file}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert C11/LKMM litmus tests into calculus litmus tests."
    )
    parser.add_argument(
        "--source-root",
        default="litmus",
        help="Source litmus root directory containing C11/ and LKMM/ subdirectories.",
    )
    parser.add_argument(
        "--output-root",
        default="converted",
        help="Output directory where converted tests and report are written.",
    )
    parser.add_argument(
        "--input-file",
        help="Convert only this specific .litmus file (strict diagnostics on unsupported operations).",
    )
    parser.add_argument(
        "--language",
        choices=["C", "Linux"],
        help="Language for --input-file when it cannot be inferred from path.",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root)
    output_root = Path(args.output_root)

    if args.input_file:
        exit_code = convert_single(Path(args.input_file), source_root, output_root, args.language)
        raise SystemExit(exit_code)

    convert_all(source_root, output_root)


if __name__ == "__main__":
    main()
