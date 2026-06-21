#!/usr/bin/env python3

from __future__ import annotations

import re
import shutil
import sys
import types
from pathlib import Path


ROOT = Path("/home/kapoorh/Documents/calculus")
SOURCE_DIR = ROOT / "litmus/paulmckrcu litmus master auto"
DEST_ROOT = ROOT / "litmus/paulmckrcu filtered"
CALCULUS_PATH = ROOT / "calculus_heap_test.py"
CLI_MARKER = "# Allow specifying the litmus input file on the command line (positional, optional)"
RESULT_RE = re.compile(r"^\s*\*\s*Result:\s*(.+?)\s*$", re.MULTILINE)
SPECIAL_WORDS = ("DATARACE", "DRF", "DEADLOCK")


def load_calculus_namespace():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    source = CALCULUS_PATH.read_text(encoding="utf-8")
    if CLI_MARKER not in source:
        raise RuntimeError(f"Could not find CLI marker in {CALCULUS_PATH}")
    library_source = source.split(CLI_MARKER, 1)[0]
    module_name = "calculus_heap_test_lib"
    module = types.ModuleType(module_name)
    module.__file__ = str(CALCULUS_PATH)
    sys.modules[module_name] = module
    exec(library_source, module.__dict__)
    return module.__dict__


def extract_result_annotation(litmus_path: Path) -> str | None:
    match = RESULT_RE.search(litmus_path.read_text(encoding="utf-8"))
    if not match:
        return None
    return match.group(1).strip()


def annotation_is_special(annotation: str | None) -> bool:
    if annotation is None:
        return False
    upper = annotation.upper()
    return any(word in upper for word in SPECIAL_WORDS)


def classify_non_rcu_forbidden(ns, litmus_path: Path):
    parsed_litmus = ns["parse_heap_file"](str(litmus_path))
    ns["global_registers"] = ns["GlobalRegisters"](len(parsed_litmus.threads))

    converted, control_guards = ns["convert_to_heap_events"](parsed_litmus.threads)
    name_to_location, initialization_events = ns["build_name_resolution_and_inits"](
        parsed_litmus.init_statements,
        parsed_litmus.thread_params,
        converted,
    )
    ns["ensure_initialized_locations"](converted, initialization_events, name_to_location)

    saw_satisfying = False
    reached_rcu_stage = False
    encountered_non_rcu_reasons: set[str] = set()

    rf_executions = ns["enumerate_rf_executions"](converted, initialization_events, name_to_location)
    for concrete_threads, concrete_inits, rf_i in rf_executions:
        filtered_threads, filtered_rf = ns["filter_control_flow_execution"](
            concrete_threads,
            rf_i,
            control_guards,
        )
        if filtered_threads is None or filtered_rf is None:
            continue

        co_candidates = ns["enumerate_co_relations"](filtered_threads, concrete_inits)
        for co_j in co_candidates:
            if not ns["constraints_satisfied"](
                filtered_threads,
                concrete_inits,
                co_j,
                parsed_litmus.constraints,
                name_to_location,
            ):
                continue

            saw_satisfying = True
            fr = ns["generate_fr_relations"](filtered_rf, co_j)
            execution = ns["Execution"](filtered_threads, filtered_rf, fr, co_j, control_guards)

            no_thin_air = ns["NoThinAir"](filtered_threads, filtered_rf)
            per_loc_sc = ns["PerLocSC"](filtered_threads, filtered_rf, fr, co_j)
            happens_before = ns["HappensBefore"](execution)
            propagates_before = ns["PropagatesBefore"](execution)

            if no_thin_air:
                encountered_non_rcu_reasons.add("NoThinAir")
            if per_loc_sc:
                encountered_non_rcu_reasons.add("PerLocSC")
            if happens_before:
                encountered_non_rcu_reasons.add("HappensBefore")
            if propagates_before:
                encountered_non_rcu_reasons.add("PropagatesBefore")

            if no_thin_air or per_loc_sc or happens_before or propagates_before:
                continue

            reached_rcu_stage = True
            if not ns["RCU"](execution):
                return {
                    "allowed": True,
                    "non_rcu_forbidden": False,
                    "reasons": set(),
                }

    non_rcu_forbidden = saw_satisfying and not reached_rcu_stage and bool(encountered_non_rcu_reasons)
    return {
        "allowed": False,
        "non_rcu_forbidden": non_rcu_forbidden,
        "reasons": encountered_non_rcu_reasons,
    }


def destination_subdir(has_special_annotation: bool, non_rcu_forbidden: bool) -> str | None:
    if has_special_annotation and non_rcu_forbidden:
        return "both"
    if has_special_annotation:
        return "comment_special"
    if non_rcu_forbidden:
        return "non_rcu_forbidden"
    return None


def main() -> int:
    ns = load_calculus_namespace()

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    moved = []
    kept = 0

    for litmus_path in sorted(SOURCE_DIR.glob("*.litmus")):
        annotation = extract_result_annotation(litmus_path)
        has_special_annotation = annotation_is_special(annotation)
        classification = classify_non_rcu_forbidden(ns, litmus_path)
        non_rcu_forbidden = classification["non_rcu_forbidden"]
        dest_subdir = destination_subdir(has_special_annotation, non_rcu_forbidden)

        if dest_subdir is None:
            kept += 1
            continue

        dest_dir = DEST_ROOT / dest_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / litmus_path.name
        shutil.move(str(litmus_path), str(dest_path))
        moved.append(
            {
                "file": litmus_path.name,
                "destination": str(dest_path.relative_to(ROOT)),
                "annotation": annotation or "MISSING",
                "non_rcu_reasons": ",".join(sorted(classification["reasons"])) or "NONE",
            }
        )

    manifest_path = DEST_ROOT / "manifest.txt"
    lines = [
        f"Source directory: {SOURCE_DIR.relative_to(ROOT)}",
        f"Moved tests: {len(moved)}",
        f"Kept tests: {kept}",
        "",
    ]
    for item in moved:
        lines.append(
            f"{item['file']} -> {item['destination']} | "
            f"annotation={item['annotation']} | non_rcu_reasons={item['non_rcu_reasons']}"
        )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Moved tests: {len(moved)}")
    print(f"Kept tests: {kept}")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
