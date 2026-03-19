#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


THREAD_HEADER_RE = re.compile(r"^(P\d+)\s*\((.*)\)\s*(\{)?\s*$")
SIMPLE_ASSIGN_RE = re.compile(
    r"^(?:[A-Za-z_][\w\s\*\(\)]*\s+)?([A-Za-z_]\w*)\s*=\s*(.+?)\s*;?$"
)
NUMERIC_RE = re.compile(r"^[+-]?\d+$")

MEMORY_ORDER_MAP = {
    "memory_order_relaxed": "Relaxed",
    "memory_order_acquire": "Acquire",
    "memory_order_release": "Release",
    "memory_order_acq_rel": "Acq_rel",
    "memory_order_seq_cst": "SEQ_CST",
}


@dataclass
class Operation:
    op: str
    location: str
    value: str
    memory_order: str
    register: Optional[str] = None

    def render(self) -> str:
        if self.op == "Read":
            return f"  Read({self.location}, None, {self.memory_order}, C, {self.register});"
        return f"  Write({self.location}, {self.value}, {self.memory_order}, C);"


@dataclass
class ThreadState:
    name: str
    params: str
    ops: List[Operation] = field(default_factory=list)
    aliases: Dict[str, str] = field(default_factory=dict)
    reg_map: Dict[str, str] = field(default_factory=dict)
    path_conditions: List[str] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)


def remove_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    while True:
        new_text = re.sub(r"\(\*.*?\*\)", "", text, flags=re.S)
        if new_text == text:
            break
        text = new_text
    text = re.sub(r"//.*$", "", text, flags=re.M)
    return text


def split_args(args_str: str) -> List[str]:
    args: List[str] = []
    current: List[str] = []
    depth = 0
    for char in args_str:
        if char == "," and depth == 0:
            args.append("".join(current).strip())
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


def parse_call(expr: str, call_name: str) -> Optional[List[str]]:
    match = re.match(rf"^\s*{re.escape(call_name)}\((.*)\)\s*;?\s*$", expr)
    if not match:
        return None
    return split_args(match.group(1))


def strip_outer_parens(expr: str) -> str:
    text = expr.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        text = text[1:-1].strip()
    return text


def normalize_identifier(expr: str) -> str:
    ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)
    return ids[-1] if ids else expr.strip()


def normalize_location(expr: str) -> str:
    text = strip_outer_parens(expr.strip())
    text = re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", text)
    text = text.lstrip("&")
    text = text.lstrip("*").strip()
    return normalize_identifier(text)


def parse_memory_order(args: List[str], index: int) -> str:
    if len(args) <= index:
        return "Relaxed"
    return MEMORY_ORDER_MAP.get(args[index].strip(), "Relaxed")


def make_unique_register(base: str, used: Dict[str, int]) -> str:
    if base not in used:
        used[base] = 1
        return base
    suffix = used[base]
    while f"{base}_{suffix}" in used:
        suffix += 1
    used[base] = suffix + 1
    unique = f"{base}_{suffix}"
    used[unique] = 1
    return unique


def resolve_alias(value: str, aliases: Dict[str, str]) -> str:
    current = value.strip()
    for _ in range(8):
        if current not in aliases:
            break
        next_value = aliases[current].strip()
        if next_value == current:
            break
        current = next_value
    return current


def normalize_value(expr: str, aliases: Dict[str, str], reg_map: Dict[str, str]) -> str:
    value = strip_outer_parens(resolve_alias(expr.strip(), aliases))
    if value in {"true", "false"}:
        return "1" if value == "true" else "0"
    if NUMERIC_RE.match(value):
        return value
    if value in reg_map:
        return reg_map[value]
    return normalize_identifier(value)


def add_condition(state: ThreadState, condition: str) -> None:
    if condition not in state.path_conditions:
        state.path_conditions.append(condition)


def note_unsupported(state: ThreadState, message: str) -> None:
    if message not in state.unsupported:
        state.unsupported.append(message)


def register_for_var(state: ThreadState, source_name: str, used_registers: Dict[str, int]) -> str:
    existing = state.reg_map.get(source_name)
    if existing:
        return existing
    unique = make_unique_register(source_name, used_registers)
    state.reg_map[source_name] = unique
    return unique


def convert_read_assignment(
    state: ThreadState,
    lhs: str,
    rhs: str,
    used_registers: Dict[str, int],
) -> Optional[Operation]:
    args = parse_call(rhs, "atomic_load_explicit")
    if args and len(args) >= 1:
        register = register_for_var(state, lhs, used_registers)
        return Operation(
            op="Read",
            location=normalize_location(args[0]),
            value="None",
            memory_order=parse_memory_order(args, 1),
            register=register,
        )

    if rhs.startswith("*") or re.match(r"^\(\s*[^()]*\)\s*\*", rhs):
        register = register_for_var(state, lhs, used_registers)
        return Operation(
            op="Read",
            location=normalize_location(rhs),
            value="None",
            memory_order="Relaxed",
            register=register,
        )

    return None


def convert_write_statement(state: ThreadState, statement: str) -> Optional[Operation]:
    args = parse_call(statement, "atomic_store_explicit")
    if args and len(args) >= 2:
        return Operation(
            op="Write",
            location=normalize_location(args[0]),
            value=normalize_value(args[1], state.aliases, state.reg_map),
            memory_order=parse_memory_order(args, 2),
        )

    plain_store = re.match(r"^\s*\*\s*(.+?)\s*=\s*(.+?)\s*;?\s*$", statement)
    if plain_store:
        return Operation(
            op="Write",
            location=normalize_location(plain_store.group(1)),
            value=normalize_value(plain_store.group(2), state.aliases, state.reg_map),
            memory_order="Relaxed",
        )

    return None


def convert_if_condition(
    state: ThreadState,
    condition_expr: str,
    used_registers: Dict[str, int],
    inherited_conditions: List[str],
) -> Optional[str]:
    expr = strip_outer_parens(condition_expr)

    equality = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", expr)
    if equality:
        lhs = strip_outer_parens(equality.group(1))
        operator = equality.group(2)
        rhs = strip_outer_parens(equality.group(3))
        if lhs.startswith("*"):
            location = normalize_location(lhs)
            cond_reg = register_for_var(state, f"if_{location}", used_registers)
            state.ops.append(
                Operation(
                    op="Read",
                    location=location,
                    value="None",
                    memory_order="Relaxed",
                    register=cond_reg,
                )
            )
            for inherited in inherited_conditions:
                add_condition(state, inherited)
            lhs_name = cond_reg
        elif lhs in state.reg_map:
            lhs_name = state.reg_map[lhs]
        else:
            lhs_name = normalize_identifier(lhs)

        rhs_value = normalize_value(rhs, state.aliases, state.reg_map)
        if operator == "==":
            return f"{lhs_name}={rhs_value}"
        note_unsupported(state, f"unsupported condition: if ({expr})")
        return None

    if expr.startswith("!"):
        inner = strip_outer_parens(expr[1:])
        if inner in state.reg_map:
            return f"{state.reg_map[inner]}=0"
        if inner.startswith("*"):
            location = normalize_location(inner)
            cond_reg = register_for_var(state, f"if_{location}", used_registers)
            state.ops.append(
                Operation(
                    op="Read",
                    location=location,
                    value="None",
                    memory_order="Relaxed",
                    register=cond_reg,
                )
            )
            for inherited in inherited_conditions:
                add_condition(state, inherited)
            return f"{cond_reg}=0"
        note_unsupported(state, f"unsupported condition: if ({expr})")
        return None

    if expr.startswith("*"):
        location = normalize_location(expr)
        cond_reg = register_for_var(state, f"if_{location}", used_registers)
        state.ops.append(
            Operation(
                op="Read",
                location=location,
                value="None",
                memory_order="Relaxed",
                register=cond_reg,
            )
        )
        for inherited in inherited_conditions:
            add_condition(state, inherited)
        return f"{cond_reg}=1"

    if expr in state.reg_map:
        return f"{state.reg_map[expr]}=1"
    if expr in state.aliases:
        resolved = normalize_value(expr, state.aliases, state.reg_map)
        return f"{resolved}=1"

    note_unsupported(state, f"unsupported condition: if ({expr})")
    return None


def process_statement(
    state: ThreadState,
    statement: str,
    active_conditions: List[str],
    used_registers: Dict[str, int],
) -> None:
    assign_match = SIMPLE_ASSIGN_RE.match(statement)
    if assign_match:
        lhs = assign_match.group(1)
        rhs = assign_match.group(2).strip()

        read_op = convert_read_assignment(state, lhs, rhs, used_registers)
        if read_op is not None:
            state.ops.append(read_op)
            for condition in active_conditions:
                add_condition(state, condition)
            return

        rhs_no_cast = re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", rhs).strip()
        if NUMERIC_RE.match(rhs_no_cast) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", rhs_no_cast):
            state.aliases[lhs] = rhs_no_cast
            return

        note_unsupported(state, f"unsupported assignment: {statement}")
        return

    write_op = convert_write_statement(state, statement)
    if write_op is not None:
        state.ops.append(write_op)
        for condition in active_conditions:
            add_condition(state, condition)
        return

    note_unsupported(state, f"unsupported statement: {statement}")


def parse_block(
    lines: List[str],
    index: int,
    state: ThreadState,
    active_conditions: List[str],
    used_registers: Dict[str, int],
) -> int:
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped == "}":
            return index + 1
        if stripped == "{":
            index += 1
            continue

        if_match = re.match(r"^if\s*\((.*)\)\s*\{\s*$", stripped)
        if if_match:
            condition = convert_if_condition(state, if_match.group(1), used_registers, active_conditions)
            next_conditions = list(active_conditions)
            if condition is not None:
                next_conditions.append(condition)
            index = parse_block(lines, index + 1, state, next_conditions, used_registers)
            continue

        if stripped.startswith("if " ) or stripped.startswith("if("):
            note_unsupported(state, f"unsupported single-line if: {stripped}")
            index += 1
            continue

        process_statement(state, stripped, active_conditions, used_registers)
        index += 1
    return index


def parse_exists_terms(raw_constraint: str, reg_maps: Dict[str, Dict[str, str]]) -> List[str]:
    text = " ".join(raw_constraint.split())
    if not text:
        return []
    if text.startswith("exists"):
        text = text[len("exists") :].strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if not text:
        return []

    reverse_map: Dict[str, List[str]] = {}
    for thread_map in reg_maps.values():
        for source, output in thread_map.items():
            reverse_map.setdefault(source, []).append(output)

    def replace_thread_prefixed(match: re.Match[str]) -> str:
        thread_name = f"P{match.group(1)}"
        source = match.group(2)
        return reg_maps.get(thread_name, {}).get(source, source)

    rewritten = re.sub(r"\b(\d+):([A-Za-z_][A-Za-z0-9_]*)\b", replace_thread_prefixed, text)

    def replace_unprefixed(match: re.Match[str]) -> str:
        token = match.group(1)
        outputs = reverse_map.get(token)
        if outputs and len(outputs) == 1:
            return outputs[0]
        return token

    rewritten = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\b(?=\s*=)", replace_unprefixed, rewritten)
    return [part.strip() for part in rewritten.split("/\\") if part.strip()]


def convert_file(input_path: Path) -> Tuple[Optional[str], List[str]]:
    text = remove_comments(input_path.read_text())
    lines = text.splitlines()

    thread_blocks: List[Tuple[str, str, List[str]]] = []
    exists_lines: List[str] = []
    used_registers: Dict[str, int] = {}

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        header_match = THREAD_HEADER_RE.match(stripped)
        if header_match:
            thread_name = header_match.group(1)
            params = header_match.group(2).strip()
            body_lines: List[str] = []
            brace_depth = 1 if header_match.group(3) else 0
            index += 1
            while index < len(lines):
                body_line = lines[index]
                brace_depth += body_line.count("{") - body_line.count("}")
                body_lines.append(body_line)
                index += 1
                if brace_depth <= 0:
                    break
            thread_blocks.append((thread_name, params, body_lines))
            continue

        if stripped.startswith("exists"):
            exists_lines = lines[index:]
            break
        index += 1

    converted_threads: List[ThreadState] = []
    for thread_name, params, body_lines in thread_blocks:
        state = ThreadState(name=thread_name, params=params)
        parse_block(body_lines, 0, state, [], used_registers)
        converted_threads.append(state)

    reg_maps = {state.name: state.reg_map for state in converted_threads}
    exists_terms = parse_exists_terms(" ".join(exists_lines), reg_maps)
    for state in converted_threads:
        for condition in state.path_conditions:
            if condition not in exists_terms:
                exists_terms.append(condition)

    unsupported = [
        f"{state.name}: {message}"
        for state in converted_threads
        for message in state.unsupported
    ]
    if unsupported:
        return None, unsupported

    output_lines: List[str] = []
    for state in converted_threads:
        output_lines.append(f"{state.name} ({state.params})" + "{")
        output_lines.extend(op.render() for op in state.ops)
        output_lines.append("}")
        output_lines.append("")

    output_lines.append("exists (" + " /\\ ".join(exists_terms) + ")")
    output_text = "\n".join(output_lines).rstrip() + "\n"
    return output_text, []


def process_dir(input_dir: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for input_file in sorted(input_dir.glob("*.litmus")):
        converted, unsupported = convert_file(input_file)
        if converted is None:
            failures += 1
            print(f"Skipping {input_file.name}:")
            for item in unsupported:
                print(f"  - {item}")
            continue
        output_path = output_dir / input_file.name
        output_path.write_text(converted)
        print(f"Wrote {output_path}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Dat3M C11 litmus tests into the Allowed_C11 converted format. "
            "If-statements are flattened into additional postconditions and plain "
            "loads/stores default to Relaxed."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="litmus/Dat3M/C11",
        help="Input directory containing Dat3M C11 .litmus files",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default="converted/Allowed_C11_auto",
        help="Output directory for converted litmus files",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")

    failures = process_dir(input_dir, output_dir)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
