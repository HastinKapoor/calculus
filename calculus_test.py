import networkx as nx
import re
import itertools
from collections import defaultdict
import copy
import argparse

from pprint import pprint

from parser_types import Operation, MemoryOrder, Language, Identifier, IterativeIdentifier, GlobalRegisters, Register
import os

class Event:
    # Identifier distinguishes two identical operations in different threads or thread positions
    # Forces values to be ints
    def __init__(self, identifier: Identifier, location, type: Operation, strength: MemoryOrder, language: Language, value: int | None, register : Register):
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
        
        # print("compose a")
        
        L = B_list[-1]
        while isinstance(L, Relation):
            L = B_list.append(L.First())
            L = B_list[-1]
        
        # print("compose b")
        
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

r1 = Relation(1, 2, 3)
r2 = Relation(4, 5, r1)
r3 = Relation(r2, 6, 7)
r4 = Relation(0, r2)

# print("First of r2:", r2.First())
# print("Last of r2:", r2.Last())

# print("r2 composes with r3:", r2.Composes(r3))
# print("r4 composes with r3:", r4.Composes(r3))

def InEmpty(rel):
    example = rel.Initial()
    
    for r in rel.elements:
        if isinstance(r, Event) and example != rel:
            return False
        elif isinstance(r, Relation) and not InEmpty(r):
            return False
    
    return True

def InID(rel):
    return rel.Composes(rel)

def InInt(threads, rel):
    i = rel.Initial()
    t = rel.Terminal()
    for thread in threads:
        if i in thread:
            return t in thread

# Cycle in communication + po-loc
def PerLocSC(threads, rf, fr, co):
    ...

def ToPoRel(threads):
    result = []
    
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].strength == MemoryOrder.RELEASE and thread[i].type != Operation.FENCE:
                for j in range(i):
                    if thread[j].type != Operation.FENCE:
                        result.append(Relation(thread[j], thread[i]))
                    else:
                        for k in range(j):
                            if thread[k].type != Operation.FENCE:
                                result.append(Relation(thread[k], thread[j], thread[i]))
            elif thread[i].strength == MemoryOrder.RELEASE and thread[i].type == Operation.FENCE:
                for j in range(i + 1, len(thread)):
                    if thread[j].type == Operation.READ or thread[j].type == Operation.RMW:
                        for k in range(i):
                            if thread[k].type != Operation.FENCE:
                                result.append(Relation(thread[k], thread[i], thread[j]))
                            else:
                                for l in range(k):
                                    if thread[l].type != Operation.FENCE:
                                        result.append(Relation(thread[l], thread[k], thread[i], thread[j]))
            
    return result

def ToAcqPo(threads):
    result = []
    
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].strength == MemoryOrder.ACQUIRE and thread[i].type != Operation.FENCE:
                for j in range(i + 1, len(thread)):
                    if thread[j].type != Operation.FENCE:
                        result.append(Relation(thread[i], thread[j]))
                    else:
                        for k in range(j + 1, len(thread)):
                            if thread[k].type != Operation.FENCE:
                                result.append(Relation(thread[i], thread[j], thread[k]))
            elif thread[i].strength == MemoryOrder.ACQUIRE and thread[i].type == Operation.FENCE:
                for j in range(i):
                    if thread[j].type == Operation.READ or thread[j].type == Operation.RMW:
                        for k in range(i + 1, len(thread)):
                            if thread[k].type != Operation.FENCE:
                                result.append(Relation(thread[j], thread[i], thread[k]))
                            else:
                                for l in range(k + 1, len(thread)):
                                    if thread[l].type != Operation.FENCE:
                                        result.append(Relation(thread[j], thread[i], thread[k], thread[l]))
                                
    
    return result

def ToStrongFence(threads):
    result = []
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].type == Operation.FENCE and thread[i].strength == MemoryOrder.SEQ_CST:
                # print("Found strong fence:", thread[i])
                for j in range(i):
                    if thread[j].type != Operation.FENCE:
                #         print("Found pre-fence event:", thread[j])
                        for k in range(i + 1, len(thread)):
                #             print("Checking post-fence event:", thread[k])
                            if thread[k].type != Operation.FENCE:
                #                 print("Found post-fence event:", thread[k])
                                result.append(Relation(thread[j], thread[i], thread[k]))
                            
    return result

def ToWMB(threads):
    result = []
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].type == Operation.FENCE and thread[i].strength == MemoryOrder.WMB:
                for j in range(i):
                    if thread[j].type == Operation.WRITE:
                        for k in range(i + 1, len(thread)):
                            if thread[k].type == Operation.WRITE:
                                result.append(Relation(thread[j], thread[i], thread[k]))
                                
    return result

def ToRMB(threads):
    result = []
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].type == Operation.FENCE and thread[i].strength == MemoryOrder.RMB:
                for j in range(i):
                    if thread[j].type == Operation.READ:
                        for k in range(i + 1, len(thread)):
                            if thread[k].type == Operation.READ:
                                result.append(Relation(thread[j], thread[i], thread[k]))
                                
    return result

# Incomplete. There exist more PPOs than will be computed here, but these are "sufficient" for small litmus tests
# po-rel, acq-po, strong-fence, WMB, RMB
def ToPPO(threads):
    result = []
    
    result += ToPoRel(threads) + ToAcqPo(threads) + ToStrongFence(threads) + ToWMB(threads) + ToRMB(threads)
    
    return result


# Incomplete. This handles rel / po-rel ; rfe ; acq / acq_po, but does not involve RMW.
def ToST(threads, rf):
    result = []
    
    rfe = []
    for r in rf:
        for thread in threads:
            if r.First() in thread and not r.Last() in thread:
                rfe.append(r)
    
    # rel ; rfe ; acq
    for r in rfe:
        if (r.First().strength == MemoryOrder.RELEASE or r.First().language == Language.LINUX) and (r.Last().strength == MemoryOrder.ACQUIRE or r.Last().language == Language.LINUX):
            result.append(r)
    
    po_rel = ToPoRel(threads)
    acq_po = ToAcqPo(threads)
    
    # po-rel ; rfe ; acq-po AND po-rel ; rfe ; acq
    for pr in po_rel:
        for r in rfe:
            if pr.Composes(r):
                for ap in acq_po:
                    if r.Composes(acq_po):
                        result.append(Relation(pr, r, ap))
                if r.Last().strength == MemoryOrder.ACQUIRE or r.Last().language == Language.LINUX:
                    result.append(Relation(pr, r))
    
    # rel ; rfe ; acq-po
    for r in rfe:
        if r.First().strength == MemoryOrder.RELEASE or r.First().language == Language.LINUX:
            for ap in acq_po:
                if r.Composes(acq_po):
                    result.append(Relation(pr, r, ap))
    
    return result

def ToProp(threads, rf, fr, co):
    result = []
    
    synct = ToST(threads, rf)
    
    tmp = rf + fr + co
    eco = tmp.copy()
    for r in tmp:
        for s in tmp:
            if r.Composes(s):
                eco.append(Relation(r, s))
    
    # Here we would compute other cumul-fences as needed
    po_rel = ToPoRel(threads)
    cumul_fences = po_rel.copy()
    for r in synct:
        for pr in po_rel:
            if r.Composes(pr):
                cumul_fences.append(Relation(r, pr))
    
    result += eco + cumul_fences + synct

    ecf = []
    est = []
    cfst = []
    
    for e in eco:
        for cf in cumul_fences:
            if e.Composes(cf):
                ecf.append(Relation(e, cf))
                
    for e in eco:
        for st in synct:
            if e.Composes(st):
                est.append(Relation(e, st))
                
    for cf in cumul_fences:
        for st in synct:
            if cf.Composes(st):
                cfst.append(Relation(cf, st))
                
    result += ecf + est + cfst
    
    for e in ecf:
        for st in synct:
            if e.Composes(st):
                result.append(Relation(e, st))
    
    return result

class Execution:
    def __init__(self, threads, rf, fr, co):
        self.threads = threads
        self.rf = rf
        self.fr = fr
        self.co = co
        self.ppo = ToPPO(threads)
        self.st = ToST(threads, rf)
        self.prop = ToProp(threads, rf, fr, co)

def ToHappensBefore(e):
    prop_int_nonempty = []
    for rel in e.prop:
        if InInt(e.threads, rel) and not InEmpty(rel):
            prop_int_nonempty.append(rel)

    rel = e.ppo + e.st + prop_int_nonempty
    
    edges = []
    
    for r in rel:
        firsts = []
        lasts = []
        
        tmp = r
        while isinstance(tmp, Relation):
            firsts.append(tmp.First())
            tmp = tmp.First()
        
        tmp = r
        while isinstance(tmp, Relation):
            lasts.append(tmp.Last())
            tmp = tmp.Last()

        for s in firsts:
            for t in lasts:
                edges.append([s, t])
                
        for s in firsts:
            edges.append([s, r])
            
        for s in lasts:
            edges.append([r, s])
    
    return edges

def HappensBefore(e):
    edges = ToHappensBefore(e)
    # for e in edges:
    #     print(e)
    G = nx.DiGraph(edges)
    return not nx.is_directed_acyclic_graph(G)

def PropagatesBefore(e):
    # print("PropagatesBefore called")
    strong_fences = ToStrongFence(e.threads)
    # for sf in strong_fences:
    #     print("Strong fence:", sf)
    
    rel = []
    edges = []
    
    for sf in strong_fences:
        for p in e.prop:
            if p.Composes(sf):
                # print(Relation(p, sf))
                rel.append(Relation(p, sf))
    
    rel += ToHappensBefore(e)
    
    for r in rel:
        firsts = []
        lasts = []
        
        tmp = r
        while isinstance(tmp, Relation):
            firsts.append(tmp.First())
            tmp = tmp.First()
        
        tmp = r
        while isinstance(tmp, Relation):
            lasts.append(tmp.Last())
            tmp = tmp.Last()

        for s in firsts:
            for t in lasts:
                edges.append([s, t])
                
        for s in firsts:
            edges.append([s, r])
            
        for s in lasts:
            edges.append([r, s])
    
    # for e in edges:
    #     print(e)
    G = nx.DiGraph(edges)
    return not nx.is_directed_acyclic_graph(G) 

# Message Passing using release and acquire accesses
# Should there exist "Events" for the initialization of x and y?
# T1_1 = Event(1, "y", Operation.WRITE, MemoryOrder.RELAXED, Language.C, 1, None)
# T1_2 = Event(2, "x", Operation.WRITE, MemoryOrder.RELEASE, Language.C, 1, None)
# T2_1 = Event(3, "x", Operation.READ, MemoryOrder.ACQUIRE, Language.C, 1, "r0")
# T2_2 = Event(4, "y", Operation.READ, MemoryOrder.RELAXED, Language.C, 0, "r1")
# T1 = [T1_1, T1_2]
# T2 = [T2_1, T2_2]
# threads = [T1, T2]
# rf = [Relation(T1_2, T2_1)]
# fr = [Relation(T2_2, T1_1)]
# co = []

# Message Passing using integers instead of Events. A first test that Happens Before and Relation are working.
# ppo = [Relation(1, 2), Relation (3, 4)]
# st = [Relation(2, 3)]
# prop_int_nonempty = [Relation(Relation(4, 1), Relation(1, 2), Relation(2, 3))] # Techincally, (1, 2), (3, 4), (2, 1), and (4, 3) are in prop, but the provided relation is the important one

# mp = Execution(threads, rf, fr, co)

# print("Is Message Passing disallowed?", HappensBefore(mp))

# An example using a more complicated prop relation
# ppo + st is almost sufficient, but the (6, 1) relation is missing (e.g. an overwrite instead of rfe) and thus the cycle requires prop
# ppo = [Relation(1, 2), Relation (3, 4), Relation(5, 6)]
# st = [Relation(2, 3), Relation(4, 5)]
# prop = [Relation(Relation(6, 1), Relation(Relation(1, 2), Relation(2, 3, 4)), Relation(4, 5))]
# prop_int_nonempty = [Relation(Relation(6, 1), Relation(Relation(1, 2), Relation(2, 3, 4)), Relation(4, 5))] #(6, 1) is eco, (4, 5) is st (i.e. rfe in Linux), and the nested relation in the middle is the cumul-fence*

# T1_1 = Event(1, "x", Operation.WRITE, MemoryOrder.RELAXED, Language.C, 2, None)
# T1_2 = Event(2, "y", Operation.WRITE, MemoryOrder.RELEASE, Language.C, 1, None)
# T2_1 = Event(3, "y", Operation.READ, MemoryOrder.ACQUIRE, Language.C, 1, "r0")
# T2_2 = Event(4, "z", Operation.WRITE, MemoryOrder.RELEASE, Language.C, 1, None)
# T3_1 = Event(5, "z", Operation.READ, MemoryOrder.ACQUIRE, Language.C, 1, "r1")
# T3_2 = Event(6, "x", Operation.WRITE, MemoryOrder.RELAXED, Language.C, 1, None)
# T1 = [T1_1, T1_2]
# T2 = [T2_1, T2_2]
# T3 = [T3_1, T3_2]
# threads = [T1, T2, T3]
# rf = [Relation(T1_2, T2_1), Relation(T2_2, T3_1)]
# fr = []
# co = [Relation(T3_2, T1_1)]

# prop_cycle = Execution(threads, rf, fr, co)
# print("Is prop_cycle disallowed?", HappensBefore(prop_cycle))
# print("Would it be disallowed without ppo?", HappensBefore(Execution([], st, prop_int_nonempty))) # No, because prop only relates from 6 to 5, ppo relates 5 to 6
# print("Would it be disallowed without st?", HappensBefore(Execution(ppo, [], prop_int_nonempty))) # Yes, because ppo + prop is sufficient in this cycle
# print("Would it be disallowed without prop?", HappensBefore(Execution(ppo, st, []))) # No

'''
x = Relation(Relation(1,2,3))
y = Relation(1,2,3)

print(x.Composes(y))
'''

'''
x = Event(0, "X", "Write", "Relaxed", "C")
y = Event(0, "X", "Write", "Relaxed", "C")
print(x == y)
'''

'''
ppo = [M] ; po ; [M & Rel] | [M & Acq] ; po ; [M]
st = [M & Rel] ; (rf & ext) ; [M & Acq]
prop = ... 
'''

def convert_to_events(parsed_threads):
    events = []

    for id, thread in enumerate(parsed_threads):
        thread_events = []
        threadIdIterator = IterativeIdentifier(id)
        for line in thread:
            event = parse_event(line, threadIdIterator.next_id())
            thread_events.append(event)
        events.append(thread_events)

    return events

def parse_event(line, identifier):

    # Remove assignment (e.g. r0 = )
    # if "=" in line:
    #     line = line.split("=", 1)[1].strip()

    # Remove trailing semicolon
    line = line.rstrip(";")

    # Match Operation(args...)
    match = re.match(r"(\w+)\((.*)\)", line)
    if not match:
        raise ValueError(f"Cannot parse line: {line}")

    _event_type = match.group(1)

    try:
        event_type = Operation[_event_type.upper()]
    except KeyError:
        print(f"{_event_type} is not a supported Operation")
    
    args = [arg.strip() for arg in match.group(2).split(",") if arg.strip()]

    location = None
    value = None
    strength = MemoryOrder.RELAXED
    language = Language.C
    register = None

    if len(args) >= 1:
        location = args[0]
    if len(args) >= 2:
        value = args[1]
    if len(args) >= 3:
        try:
            strength = MemoryOrder[args[2].upper()]
        except KeyError:
            print(f"{args[2]} is not a supported MemoryOrder/Strength")
    if len(args) >= 4:
        try:
            language = Language[args[3].upper()]
        except KeyError:
            print(f"{args[3]} is not a support Language")
    if len(args) >= 5:
        register = global_registers.newRegister(identifier.getThreadId(), args[4])

    return Event(identifier, location, event_type, strength, language, value, register)

def process_init_vals(line):
    # get rid of the braces
    line = line.replace("{", "").replace("}", "").strip()
    # split into individual inits
    inits = line.split(";")
    
    inits = [_ for _ in inits if _.strip()]
    
    initializations = []
    
    initIdIterator = IterativeIdentifier(thread_id=100)
    
    for init in inits:
        loc, val = init.split(" = ")
        
        initializations.append(Event(initIdIterator.next_id(), loc.strip(), Operation.WRITE, MemoryOrder.INITIAL, Language.C, int(val), None))
    
    return initializations
    
def parse_file(filename):
    threads = []
    current_thread = None
    initializations = []
    constraints = []

    with open(filename, "r") as f:
        for line in f:
            line = line.strip()

            if line.startswith("{") and line.endswith("}"):
                initializations = process_init_vals(line)

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
                
            elif (line.startswith("(") and line.endswith(")")) or line.startswith("exists"):
                constraints = parse_final_constraint(line)

    return threads, initializations, constraints

def parse_final_constraint(line):
    """
    Parses: (r0 = 1 /\\ r1 = 1)
    Returns: {'r0': 1, 'r1': 1}
    """
    # Remove surrounding parentheses
    line = line.replace('exists', '').strip().strip("()")
       
    or_clauses = re.split(r'\\/', line)
    
    final_clauses = []
    
    for or_clause in or_clauses:
        or_clause = or_clause.strip().strip("()")
        
        and_clauses = re.split(r'/\\', or_clause)
        
        condition_set = {}
        for clause in and_clauses:
            if '=' in clause:
                reg, val = clause.split("=")
                condition_set[reg.strip()] = int(val.strip())
                
        if condition_set:
            final_clauses.append(condition_set)

    return final_clauses

def apply_read_values(threads, read_values):
    """
    processes: list[list[Event]]
    read_values: dict like {'r0': 1, 'r1': 1}
    """
    
    # print("read_values", read_values)
    
    for thread in threads:
        for event in thread:
            # use the register's repr (e.g. 'r0') as the key into read_values
            reg_key = None
            if event.register is not None:
                try:
                    reg_key = event.register.__repr__()
                except Exception:
                    reg_key = str(event.register)

            if event.type == Operation.READ and reg_key is not None and reg_key in read_values[0]:
                event.value = read_values[0][reg_key]

def rf_candidates(processes, inits):
    """
    Returns:
      dict[ReadEvent] = [WriteEvent, ...]
    """
    loc_writes = writes_by_location(processes)
    
    # print(1, loc_writes)
    
    locations = list(loc_writes.keys())
    
    # writes = [Event(-1, loc, "Write", "Relaxed", "Linux", 0, None) for loc in locations]
    writes = inits.copy()
    reads = []

    for process in processes:
        for e in process:
            if e.type == Operation.WRITE:
                writes.append(e)
            elif e.type == Operation.READ:
                reads.append(e)
    candidates = defaultdict(list)

    # print("reads")
    # pprint(reads)
    
    # print("writes")
    # pprint(writes)

    for r in reads:
        for w in writes:
            # print(r, w)
            if str(w.location) != str(r.location):
                # print("diff location")
                continue
            # Read value constrained
            if r.value is not None:
                # print("constrained")
                if str(w.value) == str(r.value):
                    # print("constrained correct")
                    candidates[r].append(w)
            # Read value unconstrained
            else:
                # print("candidate found")
                candidates[r].append(w)
                
    # print("rf_candidates")
    # pprint(candidates)
    return candidates

def enumerate_rf_relations(processes, rf_candidates):
    """
    Returns:
      list of (rf_relation, instantiated_processes)

    rf_relation: list[Relation]
    """
    reads = list(rf_candidates.keys())
    choices = [rf_candidates[r] for r in reads]

    # print("reads", reads, "choices", choices)
    
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
    loc_writes = defaultdict(list)

    for process in processes:
        for e in process:
            if e.type == Operation.WRITE:
                loc_writes[e.location].append(e)

    return loc_writes

def co_from_order(order):
    """
    order: list[WriteEvent]
    Returns: list[Relation]
    """
    co = []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            co.append(Relation(order[i], order[j]))
    return co

def enumerate_co_relations(processes, inits):
    loc_writes = writes_by_location(processes)
    per_loc_orders = []
    
    def find_init_for_loc(loc):
        for init in inits:
            if init.location == loc:
                return init

    for loc, writes in loc_writes.items():
        init = find_init_for_loc(loc)
        
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
    rf: list[Relation]  (Write -> Read)
    co: list[Relation]  (Write -> Write)

    Returns:
      list[Relation]  (Read -> Write)
    """

    fr = []

    # Index co by source write for fast lookup
    co_from = {}
    for rel in co:
        # print("rel", rel)
        co_from.setdefault(rel.First(), []).append(rel.Last())

    # rf⁻¹ ; co
    for rf_rel in rf:
        w_prime = rf_rel.First()   # write
        r = rf_rel.Last()         # read

        for w in co_from.get(w_prime, []):
            fr.append(Relation(r, w))

    return fr

def constraints_to_strings(constraints):
    r"""Convert constraints list back to string format like (r0 = 1 /\ r1 = 1)"""
    if not constraints:
        return ""
    
    or_clauses = []
    for constraint_set in constraints:
        and_clauses = []
        for reg, val in constraint_set.items():
            and_clauses.append(f"{reg} = {val}")
        or_clauses.append(r" /\ ".join(and_clauses))
    
    if len(or_clauses) == 1:
        return f"exists ({or_clauses[0]})"
    else:
        return f"exists ({r' \/ '.join(or_clauses)})"

def inits_to_string(initializations):
    """Convert initialization events to string format like { [x] = 0; [y] = 0; }"""
    if not initializations:
        return "{}"
    
    init_strs = []
    for event in initializations:
        # Location already has brackets from parsing, use as-is
        init_strs.append(f"{event.location} = {event.value}")
    
    return "{ " + "; ".join(init_strs) + "; }"
    
def events_to_string(event):
    """Convert an Event back to string format like Read(y, None, Relaxed, C, r0);"""
    parts = [event.location]
    
    # Add value parameter
    if event.type == Operation.READ:
        parts.append("None" if event.value is None else str(event.value))
    elif event.type == Operation.WRITE:
        parts.append(str(event.value))
    
    # Add strength/memory order
    if event.strength:
        parts.append(event.strength.name.capitalize())
    
    # Add language
    if event.language:
        parts.append(event.language.name)
    
    # Add register (only for reads)
    if event.register is not None:
        parts.append(event.register.id)
    
    op_name = event.type.name.capitalize()
    return f"{op_name}({', '.join(parts)});"

# Reverse the parsing we've done, output an identical litmus test, 
# But add // comments with event IDs for each event
def extract_all_locations(converted):
    """
    Extract all unique memory locations from the converted events list.
    
    Args:
        converted: list[list[Event]] - A list of threads, where each thread is a list of Events
    
    Returns:
        list - A list of unique memory locations
    """
    mem_locations = set()
    
    for thread in converted:
        for event in thread:
            if event.type == Operation.READ or event.type == Operation.WRITE:
                mem_locations.add(event.location)
    
    return list(mem_locations)

def initialize_all_locations(initialization_events, mem_locations):
    """
    Create initialization events for memory locations that haven't been initialized.
    
    Args:
        initialization_events: list[Event] - The existing initialization events
        mem_locations: list - A list of all memory locations that need initialization
    
    Returns:
        list[Event] - Updated list of initialization events with any missing locations initialized to 0
    """
    # Get set of already initialized locations
    initialized_locs = {event.location for event in initialization_events}
    
    # Create initialization events for uninitialized locations
    initIdIterator = IterativeIdentifier(thread_id=100)
    # Move past existing init IDs
    for _ in initialization_events:
        initIdIterator.next_id()
    
    new_inits = list(initialization_events)
    
    for loc in mem_locations:
        if loc not in initialized_locs:
            new_inits.append(Event(
                initIdIterator.next_id(), 
                loc, 
                Operation.WRITE, 
                MemoryOrder.INITIAL, 
                Language.C, 
                0, 
                None
            ))
    
    return new_inits

def output_processed_litmus(inits, events, constraints):
    
    # Create 'processed' directory if it doesn't exist
    os.makedirs('processed', exist_ok=True)
    
    # Generate output filename in the 'processed' directory
    filename = os.path.basename(input_filename).split('.')
    filename[0] += "_processed"
    output_filename = os.path.join('processed', '.'.join(filename))
    
    with open(output_filename, 'w+') as f:
        # Write initializations
        f.write(inits_to_string(inits) + "\n\n")
        
        # Write threads
        for thread_idx, thread in enumerate(events):
            # Get unique location names from events in this thread
            locations = []
            seen = set()
            for event in thread:
                if event.location not in seen:
                    # Strip brackets from location for thread signature
                    loc = event.location.strip('[]')
                    locations.append(loc)
                    seen.add(event.location)
            
            # Sort locations alphabetically to match expected format
            locations.sort()
            
            f.write(f"P{thread_idx}({', '.join(locations)}) {{\n")
            
            for event in thread:
                event_str = events_to_string(event)
                f.write(f"{event_str} // Event {str(event.identifier)}\n")
            
            f.write("}\n\n")
        
        # Write constraints
        f.write(constraints_to_strings(constraints) + "\n")
        
    

print("============================================")

# Allow specifying the litmus input file on the command line (positional, optional)
parser = argparse.ArgumentParser(description="Run a litmus test file")
parser.add_argument('input', nargs='?', default='input_test.litmus', help='Path to litmus test file')
args = parser.parse_args()
input_filename = args.input

parsed, initialization_events, constraints = parse_file(input_filename)
# print(parsed)
# print(initialization_events)
# print(constraints)

global_registers = GlobalRegisters(len(parsed))

converted = convert_to_events(parsed)
# print("converted =", converted)

mem_locations = extract_all_locations(converted)
initialization_events = initialize_all_locations(initialization_events, mem_locations)


# print(initialization_events)
# print(global_registers)
output_processed_litmus(initialization_events, converted, constraints)

apply_read_values(converted, constraints)

rf = enumerate_rf_relations(converted, rf_candidates(converted, initialization_events)) # get final line from file?
print("rf", rf)
co = enumerate_co_relations(converted, initialization_events)
print("co", co)
fr = []

for rf_i in rf:
    for co_j in co:
        fr = generate_fr_relations(rf_i, co_j)
        # print("fr", fr)
        test = Execution(converted, rf_i, fr, co_j)
        print("hb", HappensBefore(test))
        print("pb", PropagatesBefore(test))
# test = Execution(converted, rf, fr, co) #[Relation(Event(1, "x", "Write", "Release", "C", 1, None), Event(2, "x", "Read", "Acquire", "C", 1, "r0")), Relation(Event(3, "y", "Write", "Relaxed", "C", 1, None), Event(0, "y", "Read", "Relaxed", "C", 1, "r1"))], [], [])

