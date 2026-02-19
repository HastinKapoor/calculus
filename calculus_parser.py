import re
import itertools
from collections import defaultdict

# TODO: co order from final constraint

class Event:
    # Identifier distinguishes two identical operations in different threads or thread positions
    def __init__(self, identifier, location, type, strength, language, value, register):
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
        return self.identifier
    
    def __repr__(self):
        return f"Event({self.identifier}, {self.location}, {self.type}, {self.strength}, {self.language}, {self.value}, {self.register})"


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
        A = self
        B = other
        
        A_list = [A]
        B_list = [B]

        L = A_list[-1]
        while isinstance(L, Relation):
            A_list.append(L.Last())
            L = A_list[-1]
        
        L = B_list[-1]
        while isinstance(L, Relation):
            L = B_list.append(L.First())
            L = B_list[-1]
        
        for R in A_list:
            if R in B_list:
                if R != A or R != B:
                    return True
        
        return False

    def __eq__(self, other):
        if not isinstance(self, Relation) or not isinstance(other, Relation):
            return type(self) == type(other) and self == other
        
        if len(self.elements) != len(other.elements):
            return False
        
        for i in range(len(self.elements)):
            if self.elements[i] != other.elements[i]:
                return False
            
        return True
    
    def __hash__(self):
        total = 0
        
        for r in self.elements:
            total += hash(r)
        
        return total
    
    def __repr__(self):
        return f"Relation({', '.join(map(str, self.elements))})"


def convert_to_events(parsed_threads):
    """
    Convert parsed thread strings to Event objects.
    
    Args:
        parsed_threads: list[list[str]] - threads with statement strings
        
    Returns:
        list[list[Event]] - threads with Event objects
    """
    events = []
    event_id = 0

    for thread in parsed_threads:
        thread_events = []
        for line in thread:
            event = parse_event(line, event_id)
            thread_events.append(event)
            event_id += 1
        events.append(thread_events)

    return events


def parse_event(line, identifier):
    """
    Parse a single event statement into an Event object.
    
    Args:
        line: str - statement like "Write(x, 1, Release, C)"
        identifier: int - unique event ID
        
    Returns:
        Event object
    """
    # Remove assignment (e.g. r0 = )
    # if "=" in line:
    #     line = line.split("=", 1)[1].strip()

    # Remove trailing semicolon
    line = line.rstrip(";")

    # Match Operation(args...)
    match = re.match(r"(\w+)\((.*)\)", line)
    if not match:
        raise ValueError(f"Cannot parse line: {line}")

    event_type = match.group(1)
    args = [arg.strip() for arg in match.group(2).split(",") if arg.strip()]

    location = None
    value = None
    strength = None
    language = None
    register = None

    if len(args) >= 1:
        location = args[0]
    if len(args) >= 2:
        value = args[1]
    if len(args) >= 3:
        strength = args[2]
    if len(args) >= 4:
        language = args[3]
    if len(args) >= 5:
        register = args[4]

    return Event(identifier, location, event_type, strength, language, value, register)


def parse_file(filename):
    """
    Parse a litmus test file with thread blocks.
    
    Expected format:
        P0 {
            statement1;
            statement2;
        }
        P1 {
            statement3;
        }
    
    Args:
        filename: str - path to input file
        
    Returns:
        list[list[str]] - threads with statement strings
    """
    threads = []
    current_thread = None

    with open(filename, "r") as f:
        for line in f:
            line = line.strip()

            # Start of a new thread
            if line.startswith("P") and line.endswith("{"):
                current_thread = []

            # End of a thread
            elif line == "}":
                if current_thread is not None:
                    threads.append(current_thread)
                    current_thread = None

            # Inside a thread: collect statements
            elif current_thread is not None and line:
                current_thread.append(line)

    return threads


def parse_final_constraint(line):
    """
    Parse final constraint expression.
    
    Example: "(r0 = 1 /\ r1 = 1)" -> {'r0': 1, 'r1': 1}
    
    Args:
        line: str - constraint expression
        
    Returns:
        dict[str, int] - register -> expected value mapping
    """
    line = line.strip()

    # Remove surrounding parentheses
    if line.startswith("(") and line.endswith(")"):
        line = line[1:-1]

    clauses = [c.strip() for c in line.split("/\\")]
    result = {}

    for clause in clauses:
        reg, val = clause.split("=")
        result[reg.strip()] = int(val.strip())

    return result


def apply_read_values(threads, read_values):
    """
    Apply read value constraints to events.
    
    Args:
        threads: list[list[Event]] - threads with events
        read_values: dict[str, int] - register -> value mapping
    """
    for thread in threads:
        for event in thread:
            if event.type == "Read" and event.register in read_values:
                event.value = read_values[event.register]


def rf_candidates(processes):
    """
    Generate all possible reads-from candidates.
    
    Args:
        processes: list[list[Event]] - threads with events
        
    Returns:
        dict[Event, list[Event]] - read -> list of possible writes
    """
    loc_writes = writes_by_location(processes)
    locations = list(loc_writes.keys())
    
    # Create initialization writes for each location
    writes = [Event(-1, loc, "Write", "Relaxed", "Linux", 0, None) for loc in locations]
    reads = []

    for process in processes:
        for e in process:
            if e.type == "Write":
                writes.append(e)
            elif e.type == "Read":
                reads.append(e)
    
    candidates = defaultdict(list)

    for r in reads:
        for w in writes:
            print(r, w)
            if str(w.location) != str(r.location):
                print("diff location")
                continue
            # Read value constrained
            if r.value != "None":
                print("constrained")
                if str(w.value) == str(r.value):
                    print("constrained correct")
                    candidates[r].append(w)
            # Read value unconstrained
            else:
                print("yay!")
                candidates[r].append(w)
                
    print("rf_candidates", candidates)
    return candidates


def enumerate_rf_relations(processes, rf_candidates):
    """
    Enumerate all possible reads-from relations.
    
    Args:
        processes: list[list[Event]] - threads with events
        rf_candidates: dict[Event, list[Event]] - read -> possible writes
        
    Returns:
        list[list[Relation]] - list of all possible rf relation sets
    """
    reads = list(rf_candidates.keys())
    choices = [rf_candidates[r] for r in reads]
    
    results = []

    for selection in itertools.product(*choices):
        rf = []

        # Map original read → chosen write
        choice_map = dict(zip(reads, selection))

        # Build rf relations + propagate values
        for process in processes:
            for e in process:
                if e in choice_map:
                    w = choice_map[e]
                    e.value = w.value
                    rf.append(Relation(w, e))
        
        results.append(rf)
    
    return results


def writes_by_location(processes):
    """
    Group writes by memory location.
    
    Args:
        processes: list[list[Event]] - threads with events
        
    Returns:
        dict[str, list[Event]] - location -> list of writes
    """
    loc_writes = defaultdict(list)

    for process in processes:
        for e in process:
            if e.type == "Write":
                loc_writes[e.location].append(e)

    return loc_writes


def co_from_order(order):
    """
    Generate coherence relations from a write order.
    
    Args:
        order: list[Event] - ordered list of writes
        
    Returns:
        list[Relation] - coherence order relations
    """
    co = []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            co.append(Relation(order[i], order[j]))
    return co


def enumerate_co_relations(processes):
    """
    Enumerate all possible coherence orders.
    
    Args:
        processes: list[list[Event]] - threads with events
        
    Returns:
        list[list[Relation]] - list of all possible co relation sets
    """
    loc_writes = writes_by_location(processes)
    per_loc_orders = []

    for loc, writes in loc_writes.items():
        # Create initialization write
        init = Event(-1, loc, "Write", "Relaxed", "Linux", 0, None)

        if len(writes) <= 1:
            # One possible order
            per_loc_orders.append([[init] + writes])
        else:
            # Many possible orders
            orders = [
                [init] + list(p)
                for p in itertools.permutations(writes)
            ]
            per_loc_orders.append(orders)

    all_cos = []

    for selection in itertools.product(*per_loc_orders):
        co = []
        for order in selection:
            co.extend(co_from_order(order))
        all_cos.append(co)

    return all_cos


def generate_fr_relations(rf, co):
    """
    Generate from-reads relations from rf and co.
    
    Computes: rf⁻¹ ; co (read followed by a later write to the same location)
    
    Args:
        rf: list[Relation] - reads-from relations (Write -> Read)
        co: list[Relation] - coherence order relations (Write -> Write)
        
    Returns:
        list[Relation] - from-reads relations (Read -> Write)
    """
    fr = []

    # Index co by source write for fast lookup
    co_from = {}
    for rel in co:
        print("rel", rel)
        co_from.setdefault(rel.First(), []).append(rel.Last())

    # rf⁻¹ ; co
    for rf_rel in rf:
        w_prime = rf_rel.First()   # write
        r = rf_rel.Last()         # read

        for w in co_from.get(w_prime, []):
            fr.append(Relation(r, w))

    return fr
