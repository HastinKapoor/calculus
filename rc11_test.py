"""
RC11-style axiomatic checker for the converted litmus tests in this repository.

This keeps the same overall feel as calculus_test.py:
- explicit Event and Relation objects
- enumeration of rf/mo candidates
- graph-based acyclicity checks

The key difference is that the derived relations are named after the RC11/C11
specification:
- sb: sequenced-before
- rf: reads-from
- mo: modification order
- fr: from-read
- rs: release sequence
- sw: synchronizes-with
- hb: happens-before

The implementation focuses on the fragment used by the converted litmus tests:
atomic reads, writes, and fences, with explicit memory orders.
"""

import argparse
import copy
import itertools
import re
from collections import defaultdict

import networkx as nx

from parser_types import (
    GlobalRegisters,
    Identifier,
    IterativeIdentifier,
    Language,
    MemoryOrder,
    Operation,
    Register,
)


class Event:
    # Identifier distinguishes identical operations in different threads/positions.
    def __init__(
        self,
        identifier: Identifier,
        location,
        type: Operation,
        strength: MemoryOrder,
        language: Language,
        value: int | None,
        register: Register,
    ):
        self.identifier = identifier
        self.location = location
        self.type = type
        self.strength = strength
        self.language = language
        self.value = value
        self.register = register

    def __eq__(self, other):
        if not isinstance(self, Event) or not isinstance(other, Event):
            return type(self) == type(other) and self == other
        return self.identifier == other.identifier and self.location == other.location

    def __hash__(self):
        return hash(self.identifier)

    def __repr__(self):
        return (
            "Event("
            f"{self.identifier}, {self.location}, {self.type}, {self.strength}, "
            f"{self.language}, {self.value}, {self.register})"
        )


class Relation:
    def __init__(self, *args):
        self.elements = list(args)

    def First(self):
        return self.elements[0]

    def Last(self):
        return self.elements[-1]

    def Initial(self):
        result = self.First()
        while isinstance(result, Relation):
            result = result.First()
        return result

    def Terminal(self):
        result = self.Last()
        while isinstance(result, Relation):
            result = result.Last()
        return result

    def Composes(self, other):
        a_list = [self]
        b_list = [other]

        current = a_list[-1]
        while isinstance(current, Relation):
            a_list.append(current.Last())
            current = a_list[-1]

        current = b_list[-1]
        while isinstance(current, Relation):
            b_list.append(current.First())
            current = b_list[-1]

        for rel in a_list:
            if rel in b_list and (rel != self or rel != other):
                return True
        return False

    def __eq__(self, other):
        if not isinstance(self, Relation) or not isinstance(other, Relation):
            return type(self) == type(other) and self == other
        return self.elements == other.elements

    def __hash__(self):
        return sum(hash(rel) for rel in self.elements)

    def __repr__(self):
        return f"Relation({', '.join(map(str, self.elements))})"


def relation_edges(relations):
    return [(rel.First(), rel.Last()) for rel in relations]


def canonicalize_location(location):
    return str(location).strip().strip("[]")


def is_read(event):
    return event.type in {Operation.READ, Operation.RMW, Operation.RMW_R}


def is_write(event):
    return event.type in {Operation.WRITE, Operation.RMW, Operation.RMW_W}


def is_fence(event):
    return event.type == Operation.FENCE


def is_rmw(event):
    return event.type in {Operation.RMW, Operation.RMW_R, Operation.RMW_W}


def is_release(event):
    return event.strength in {
        MemoryOrder.RELEASE,
        MemoryOrder.ACQ_REL,
        MemoryOrder.SEQ_CST,
    }


def is_acquire(event):
    return event.strength in {
        MemoryOrder.ACQUIRE,
        MemoryOrder.ACQ_REL,
        MemoryOrder.SEQ_CST,
    }


def is_seq_cst(event):
    return event.strength == MemoryOrder.SEQ_CST


def same_thread(left, right):
    return left.identifier.getThreadId() == right.identifier.getThreadId()


def process_init_vals(line):
    line = line.replace("{", "").replace("}", "").strip()
    inits = [item for item in line.split(";") if item.strip()]

    initializations = []
    init_ids = IterativeIdentifier(thread_id=100)
    for init in inits:
        loc, val = init.split(" = ")
        initializations.append(
            Event(
                init_ids.next_id(),
                loc.strip(),
                Operation.WRITE,
                MemoryOrder.INITIAL,
                Language.C,
                int(val),
                None,
            )
        )
    return initializations


def parse_final_constraint(line):
    line = line.replace("exists", "").strip().strip("()")
    or_clauses = re.split(r"\\/", line)

    parsed = []
    for clause in or_clauses:
        clause = clause.strip().strip("()")
        conjuncts = re.split(r"/\\", clause)

        values = {}
        for conjunct in conjuncts:
            if "=" not in conjunct:
                continue
            key, value = conjunct.split("=")
            values[key.strip()] = int(value.strip())

        if values:
            parsed.append(values)
    return parsed


def parse_file(filename):
    threads = []
    current_thread = None
    initializations = []
    constraints = []

    with open(filename, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if line.startswith("{") and line.endswith("}"):
                initializations = process_init_vals(line)
            elif line.startswith("P") and line.endswith("{"):
                current_thread = []
            elif line == "}":
                if current_thread is not None:
                    threads.append(current_thread)
                    current_thread = None
            elif current_thread is not None and line:
                current_thread.append(line)
            elif (line.startswith("(") and line.endswith(")")) or line.startswith("exists"):
                constraints = parse_final_constraint(line)

    return threads, initializations, constraints


def parse_event(line, identifier):
    line = line.rstrip(";")

    match = re.match(r"(\w+)\((.*)\)", line)
    if not match:
        raise ValueError(f"Cannot parse line: {line}")

    op_name = match.group(1)
    try:
        event_type = Operation[op_name.upper()]
    except KeyError as exc:
        raise ValueError(f"Unsupported operation: {op_name}") from exc

    args = [arg.strip() for arg in match.group(2).split(",") if arg.strip()]

    location = None
    value = None
    strength = MemoryOrder.RELAXED
    language = Language.C
    register = None

    if len(args) >= 1:
        location = args[0]
    if len(args) >= 2 and args[1] != "None":
        value = int(args[1])
    if len(args) >= 3:
        strength = MemoryOrder[args[2].upper()]
    if len(args) >= 4:
        language = Language[args[3].upper()]
    if len(args) >= 5 and args[4] != "None":
        register = global_registers.newRegister(identifier.getThreadId(), args[4])

    return Event(identifier, location, event_type, strength, language, value, register)


def convert_to_events(parsed_threads):
    events = []
    for thread_id, thread in enumerate(parsed_threads):
        thread_events = []
        identifiers = IterativeIdentifier(thread_id)
        for line in thread:
            thread_events.append(parse_event(line, identifiers.next_id()))
        events.append(thread_events)
    return events


def extract_all_locations(threads):
    locations = set()
    for thread in threads:
        for event in thread:
            if event.location is not None and (is_read(event) or is_write(event)):
                locations.add(event.location)
    return list(locations)


def initialize_all_locations(initializations, locations):
    initialized = {event.location for event in initializations}
    ids = IterativeIdentifier(thread_id=100)
    for _ in initializations:
        ids.next_id()

    completed = list(initializations)
    for loc in locations:
        if loc in initialized:
            continue
        completed.append(
            Event(
                ids.next_id(),
                loc,
                Operation.WRITE,
                MemoryOrder.INITIAL,
                Language.C,
                0,
                None,
            )
        )
    return completed


def apply_constraint_clause(threads, clause):
    for thread in threads:
        for event in thread:
            if event.register is None:
                continue
            reg_name = event.register.__repr__()
            if is_read(event) and reg_name in clause:
                event.value = clause[reg_name]


def writes_by_location(threads):
    by_location = defaultdict(list)
    for thread in threads:
        for event in thread:
            if is_write(event):
                by_location[canonicalize_location(event.location)].append(event)
    return by_location


def rf_candidates(threads, initializations):
    writes = list(initializations)
    reads = []

    for thread in threads:
        for event in thread:
            if is_write(event):
                writes.append(event)
            elif is_read(event):
                reads.append(event)

    candidates = defaultdict(list)
    for read in reads:
        for write in writes:
            if canonicalize_location(write.location) != canonicalize_location(read.location):
                continue
            if read.value is None or str(write.value) == str(read.value):
                candidates[read].append(write)
    return candidates


def enumerate_rf_relations(threads, candidates):
    reads = list(candidates.keys())
    choices = [candidates[read] for read in reads]
    results = []

    for selection in itertools.product(*choices):
        rf = []
        choice_map = dict(zip(reads, selection))
        for read, write in choice_map.items():
            read.value = write.value
            rf.append(Relation(write, read))
        results.append(rf)
    return results


def extract_location_constraints(constraints, initializations, threads):
    known_locations = {
        canonicalize_location(init.location)
        for init in initializations
        if init.location is not None
    }

    for thread in threads:
        for event in thread:
            if event.location is not None:
                known_locations.add(canonicalize_location(event.location))

    location_constraints = []
    for clause in constraints:
        filtered = {}
        for key, value in clause.items():
            normalized = canonicalize_location(key)
            if normalized in known_locations:
                filtered[normalized] = value
        location_constraints.append(filtered)

    return location_constraints


def co_satisfies_constraints(selection, constraints, initializations, threads):
    location_constraints = extract_location_constraints(constraints, initializations, threads)
    if not location_constraints:
        return True

    finals = {
        canonicalize_location(init.location): init.value
        for init in initializations
    }
    for order in selection:
        if order:
            finals[canonicalize_location(order[0].location)] = order[-1].value

    constrained = [clause for clause in location_constraints if clause]
    if not constrained:
        return True

    for clause in constrained:
        if all(str(finals.get(loc)) == str(value) for loc, value in clause.items()):
            return True
    return False


def mo_from_order(order):
    relations = []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            relations.append(Relation(order[i], order[j]))
    return relations


def enumerate_mo_relations(threads, initializations, constraints=None):
    per_location_orders = []
    loc_writes = writes_by_location(threads)

    def init_for_location(location):
        for init in initializations:
            if canonicalize_location(init.location) == location:
                return init
        return None

    for location, writes in loc_writes.items():
        init = init_for_location(location)
        if len(writes) <= 1:
            per_location_orders.append([[init] + writes])
        else:
            per_location_orders.append(
                [[init] + list(order) for order in itertools.permutations(writes)]
            )

    results = []
    for selection in itertools.product(*per_location_orders):
        if constraints and not co_satisfies_constraints(selection, constraints, initializations, threads):
            continue

        mo = []
        for order in selection:
            mo.extend(mo_from_order(order))
        results.append(mo)
    return results


def generate_fr_relations(rf, mo):
    fr = []
    mo_from = defaultdict(list)
    for rel in mo:
        mo_from[rel.First()].append(rel.Last())

    for rf_rel in rf:
        source = rf_rel.First()
        read = rf_rel.Last()
        for later_write in mo_from.get(source, []):
            fr.append(Relation(read, later_write))
    return fr


def thread_positions(threads):
    positions = {}
    for thread in threads:
        for index, event in enumerate(thread):
            positions[event] = index
    return positions


def sb_relations(threads):
    relations = []
    for thread in threads:
        for i in range(len(thread)):
            for j in range(i + 1, len(thread)):
                relations.append(Relation(thread[i], thread[j]))
    return relations


def sb_loc_relations(threads):
    relations = []
    for thread in threads:
        for i in range(len(thread)):
            for j in range(i + 1, len(thread)):
                left = thread[i]
                right = thread[j]
                if left.location is None or right.location is None:
                    continue
                if canonicalize_location(left.location) == canonicalize_location(right.location):
                    relations.append(Relation(left, right))
    return relations


def relation_graph(relations):
    graph = nx.DiGraph()
    graph.add_edges_from(relation_edges(relations))
    return graph


def reachable(graph, source, target):
    if source == target:
        return True
    return nx.has_path(graph, source, target)


def release_sequence_heads(write, mo_graph):
    # RC11 release sequence:
    # start at a write, then follow modification order through same-thread writes,
    # plus any intervening RMWs. This is enough for the converted litmus tests and
    # leaves the relation named explicitly as release sequence (rs).
    sequence = {write}
    worklist = [write]

    while worklist:
        current = worklist.pop()
        for successor in mo_graph.successors(current):
            if canonicalize_location(successor.location) != canonicalize_location(write.location):
                continue
            if successor in sequence:
                continue
            if same_thread(write, successor) or is_rmw(successor):
                sequence.add(successor)
                worklist.append(successor)

    return sequence


def rf_external(rf):
    return [rel for rel in rf if not same_thread(rel.First(), rel.Last())]


class RC11Execution:
    def __init__(self, threads, initializations, rf, mo):
        self.threads = threads
        self.initializations = initializations
        self.rf = rf
        self.mo = mo
        self.fr = generate_fr_relations(rf, mo)

        self.sb = sb_relations(threads)
        self.sb_loc = sb_loc_relations(threads)

        self.thread_index = thread_positions(threads)
        self.mo_graph = relation_graph(mo)
        self.sb_graph = relation_graph(self.sb)

        self.rs = self.compute_release_sequence()
        self.sw = self.compute_synchronizes_with()
        self.hb = self.compute_happens_before()
        self.eco = self.rf + self.fr + self.mo
        self.sc = self.compute_sc_order_constraints()

    def sb_before(self, left, right):
        if not same_thread(left, right):
            return False
        return self.thread_index[left] < self.thread_index[right]

    def compute_release_sequence(self):
        rs = []
        writes = list(self.initializations)
        for thread in self.threads:
            writes.extend(event for event in thread if is_write(event))

        for head in writes:
            members = release_sequence_heads(head, self.mo_graph)
            for member in members:
                rs.append(Relation(head, member))
        return rs

    def release_sequence_members(self, head):
        return {rel.Last() for rel in self.rs if rel.First() == head}

    def compute_synchronizes_with(self):
        sw = []
        rfe = rf_external(self.rf)

        writes = [event for thread in self.threads for event in thread if is_write(event)]
        reads = [event for thread in self.threads for event in thread if is_read(event)]
        fences = [event for thread in self.threads for event in thread if is_fence(event)]
        release_fences = [event for event in fences if is_release(event)]
        acquire_fences = [event for event in fences if is_acquire(event)]

        for rf_rel in rfe:
            source_write = rf_rel.First()
            read = rf_rel.Last()

            for head in writes + list(self.initializations):
                if source_write not in self.release_sequence_members(head):
                    continue

                if is_release(head) and is_acquire(read):
                    sw.append(Relation(head, read))

                for release_fence in release_fences:
                    if self.sb_before(release_fence, head) and is_acquire(read):
                        sw.append(Relation(release_fence, read))

                    for acquire_fence in acquire_fences:
                        if self.sb_before(release_fence, head) and self.sb_before(read, acquire_fence):
                            sw.append(Relation(release_fence, acquire_fence))

                for acquire_fence in acquire_fences:
                    if is_release(head) and self.sb_before(read, acquire_fence):
                        sw.append(Relation(head, acquire_fence))

        # Remove duplicates and fence self-relations that do not add information.
        unique = []
        seen = set()
        for rel in sw:
            if rel.First() == rel.Last():
                continue
            key = (rel.First(), rel.Last())
            if key in seen:
                continue
            seen.add(key)
            unique.append(rel)
        return unique

    def compute_happens_before(self):
        base_graph = relation_graph(self.sb + self.sw)
        if not nx.is_directed_acyclic_graph(base_graph):
            return self.sb + self.sw

        closure = nx.transitive_closure_dag(base_graph)
        hb = []
        for source, target in closure.edges():
            hb.append(Relation(source, target))
        return hb

    def compute_sc_order_constraints(self):
        sc_events = [
            event
            for thread in self.threads
            for event in thread
            if is_seq_cst(event)
        ]
        if not sc_events:
            return []

        constraints = []
        sc_set = set(sc_events)
        hb_graph = relation_graph(self.hb)

        for source in sc_events:
            for target in sc_events:
                if source != target and reachable(hb_graph, source, target):
                    constraints.append(Relation(source, target))

        for rel in self.mo:
            if rel.First() in sc_set and rel.Last() in sc_set:
                constraints.append(rel)

        sc_writes = [event for event in sc_events if is_write(event)]

        for rf_rel in self.rf:
            source = rf_rel.First()
            read = rf_rel.Last()
            if read not in sc_set:
                continue

            if source in sc_set:
                constraints.append(Relation(source, read))

            for candidate in sc_writes:
                if canonicalize_location(candidate.location) != canonicalize_location(read.location):
                    continue
                if source == candidate:
                    continue
                if source in self.mo_graph and candidate in self.mo_graph and reachable(self.mo_graph, source, candidate):
                    constraints.append(Relation(read, candidate))

        unique = []
        seen = set()
        for rel in constraints:
            key = (rel.First(), rel.Last())
            if rel.First() == rel.Last() or key in seen:
                continue
            seen.add(key)
            unique.append(rel)
        return unique

    def per_location_sc(self):
        return nx.is_directed_acyclic_graph(
            relation_graph(self.sb_loc + self.rf + self.fr + self.mo)
        )

    def hb_acyclic(self):
        return nx.is_directed_acyclic_graph(relation_graph(self.hb))

    def hb_eco_acyclic(self):
        return nx.is_directed_acyclic_graph(relation_graph(self.hb + self.eco))

    def sc_order_exists(self):
        return nx.is_directed_acyclic_graph(relation_graph(self.sc))

    def consistent(self):
        c_no_thin_air = [
            rel
            for rel in self.sb + self.rf
            if rel.First().language == Language.C and rel.Last().language == Language.C
        ]
        return (
            nx.is_directed_acyclic_graph(relation_graph(c_no_thin_air))
            and self.per_location_sc()
            and self.hb_acyclic()
            and self.hb_eco_acyclic()
            and self.sc_order_exists()
        )


def evaluate_clause(threads, initializations, clause, constraints):
    clause_threads = copy.deepcopy(threads)
    apply_constraint_clause(clause_threads, clause)

    candidates = rf_candidates(clause_threads, initializations)
    rf_choices = enumerate_rf_relations(clause_threads, candidates)
    mo_choices = enumerate_mo_relations(clause_threads, initializations, constraints)

    for rf in rf_choices:
        for mo in mo_choices:
            execution = RC11Execution(clause_threads, initializations, rf, mo)
            if execution.consistent():
                return execution
    return None


def format_relation(name, relations):
    return f"{name} = {relations}"


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate converted litmus tests against an RC11-style axiomatic model"
    )
    parser.add_argument("input", help="Path to a converted .litmus file")
    parser.add_argument(
        "--show-relations",
        action="store_true",
        help="Print the witness execution relations when the outcome is allowed",
    )
    args = parser.parse_args()

    parsed_threads, initializations, constraints = parse_file(args.input)

    global global_registers
    global_registers = GlobalRegisters(len(parsed_threads))

    threads = convert_to_events(parsed_threads)
    initializations = initialize_all_locations(initializations, extract_all_locations(threads))

    clauses = constraints if constraints else [{}]
    witness = None
    matched_clause = None

    for clause in clauses:
        execution = evaluate_clause(threads, initializations, clause, constraints)
        if execution is not None:
            witness = execution
            matched_clause = clause
            break

    if witness is None:
        print(f"{args.input}: Forbidden")
        return

    print(f"{args.input}: Allowed")
    # if matched_clause is not None:
        # print(f"matched exists clause: {matched_clause}")

    if args.show_relations:
        print(format_relation("sb", witness.sb))
        print(format_relation("rf", witness.rf))
        print(format_relation("mo", witness.mo))
        print(format_relation("fr", witness.fr))
        print(format_relation("rs", witness.rs))
        print(format_relation("sw", witness.sw))
        print(format_relation("hb", witness.hb))
        print(format_relation("sc-constraints", witness.sc))


if __name__ == "__main__":
    main()
