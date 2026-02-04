import networkx as nx
import re
import itertools
from collections import defaultdict
import copy

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

print("First of r2:", r2.First())
print("Last of r2:", r2.Last())

print("r2 composes with r3:", r2.Composes(r3))
print("r4 composes with r3:", r4.Composes(r3))

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
            if thread[i].strength == "Release" and thread[i].type != "Fence":
                for j in range(i):
                    if thread[j].type != "Fence":
                        result.append(Relation(thread[j], thread[i]))
                    else:
                        for k in range(j):
                            if thread[k].type != "Fence":
                                result.append(Relation(thread[k], thread[j], thread[i]))
            elif thread[i].strength == "Release" and thread[i].type == "Fence":
                for j in range(i + 1, len(thread)):
                    if thread[j].type == "Read" or thread[j].type == "RMW":
                        for k in range(i):
                            if thread[k].type != "Fence":
                                result.append(Relation(thread[k], thread[i], thread[j]))
                            else:
                                for l in range(k):
                                    if thread[l].type != "Fence":
                                        result.append(Relation(thread[l], thread[k], thread[i], thread[j]))
            
    return result

def ToAcqPo(threads):
    result = []
    
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].strength == "Acquire" and thread[i].type != "Fence":
                for j in range(i + 1, len(thread)):
                    if thread[j].type != "Fence":
                        result.append(Relation(thread[i], thread[j]))
                    else:
                        for k in range(j + 1, len(thread)):
                            if thread[k].type != "Fence":
                                result.append(Relation(thread[i], thread[j], thread[k]))
            elif thread[i].strength == "Acquire" and thread[i].type == "Fence":
                for j in range(i):
                    if thread[j].type == "Read" or thread[j].type == "RMW":
                        for k in range(i + 1, len(thread)):
                            if thread[k].type != "Fence":
                                result.append(Relation(thread[j], thread[i], thread[k]))
                            else:
                                for l in range(k + 1, len(thread)):
                                    if thread[l].type != "Fence":
                                        result.append(Relation(thread[j], thread[i], thread[k], thread[l]))
                                
    
    return result

def ToStrongFence(threads):
    result = []
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].type == "Fence" and thread[i].strength == "SC":
                for j in range(i):
                    if thread[j].type != "Fence":
                        for k in range(i + 1, len(thread)):
                            if thread[k].type != "Fence":
                                result.append(Relation(thread[j], thread[i], thread[k]))
                            
    return result

# Incomplete. There exist more PPOs than will be computed here, but these are "sufficient" for small litmus tests
# po-rel, acq-po, strong-fence
def ToPPO(threads):
    result = []
    
    result += ToPoRel(threads) + ToAcqPo(threads) + ToStrongFence(threads)
    
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
        if (r.First().strength == "Release" or r.First().language == "Linux") and (r.Last().strength == "Acquire" or r.Last().language == "Linux"):
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
                if r.Last().strength == "Acquire" or r.Last().language == "Linux":
                    result.append(Relation(pr, r))
    
    # rel ; rfe ; acq-po
    for r in rfe:
        if r.First().strength == "Release" or r.First().language == "Linux":
            for ap in acq_po:
                if r.Composes(acq_po):
                    result.append(Relation(pr, r, ap))
    
    return result

def ToProp(threads, rf, fr, co):
    result = []
    
    synct = ToST(threads, rf)
    
    eco = rf + fr + co
    for r in eco:
        eco += []
    
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
    strong_fences = ToStrongFence(e.threads)
    
    rel = []
    edges = []
    
    for sf in strong_fences:
        for p in e.prop:
            if p.Composes(sf):
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
T1_1 = Event(1, "y", "Write", "Relaxed", "C", 1, None)
T1_2 = Event(2, "x", "Write", "Release", "C", 1, None)
T2_1 = Event(3, "x", "Read", "Acquire", "C", 1, "r0")
T2_2 = Event(4, "y", "Read", "Relaxed", "C", 0, "r1")
T1 = [T1_1, T1_2]
T2 = [T2_1, T2_2]
threads = [T1, T2]
rf = [Relation(T1_2, T2_1)]
fr = [Relation(T2_2, T1_1)]
co = []

# Message Passing using integers instead of Events. A first test that Happens Before and Relation are working.
ppo = [Relation(1, 2), Relation (3, 4)]
st = [Relation(2, 3)]
prop_int_nonempty = [Relation(Relation(4, 1), Relation(1, 2), Relation(2, 3))] # Techincally, (1, 2), (3, 4), (2, 1), and (4, 3) are in prop, but the provided relation is the important one

mp = Execution(threads, rf, fr, co)

print("Is Message Passing disallowed?", HappensBefore(mp))

# An example using a more complicated prop relation
# ppo + st is almost sufficient, but the (6, 1) relation is missing (e.g. an overwrite instead of rfe) and thus the cycle requires prop
# ppo = [Relation(1, 2), Relation (3, 4), Relation(5, 6)]
# st = [Relation(2, 3), Relation(4, 5)]
# prop = [Relation(Relation(6, 1), Relation(Relation(1, 2), Relation(2, 3, 4)), Relation(4, 5))]
# prop_int_nonempty = [Relation(Relation(6, 1), Relation(Relation(1, 2), Relation(2, 3, 4)), Relation(4, 5))] #(6, 1) is eco, (4, 5) is st (i.e. rfe in Linux), and the nested relation in the middle is the cumul-fence*

T1_1 = Event(1, "x", "Write", "Relaxed", "C", 2, None)
T1_2 = Event(2, "y", "Write", "Release", "C", 1, None)
T2_1 = Event(3, "y", "Read", "Acquire", "C", 1, "r0")
T2_2 = Event(4, "z", "Write", "Release", "C", 1, None)
T3_1 = Event(5, "z", "Read", "Acquire", "C", 1, "r1")
T3_2 = Event(6, "x", "Write", "Relaxed", "C", 1, None)
T1 = [T1_1, T1_2]
T2 = [T2_1, T2_2]
T3 = [T3_1, T3_2]
threads = [T1, T2, T3]
rf = [Relation(T1_2, T2_1), Relation(T2_2, T3_1)]
fr = []
co = [Relation(T3_2, T1_1)]

prop_cycle = Execution(threads, rf, fr, co)
print("Is prop_cycle disallowed?", HappensBefore(prop_cycle))
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
    Parses: (r0 = 1 /\ r1 = 1)
    Returns: {'r0': 1, 'r1': 1}
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
    processes: list[list[Event]]
    read_values: dict like {'r0': 1, 'r1': 1}
    """
    for thread in threads:
        for event in thread:
            if event.type == "Read" and event.register in read_values:
                event.value = read_values[event.register]

def rf_candidates(processes):
    """
    Returns:
      dict[ReadEvent] = [WriteEvent, ...]
    """
    loc_writes = writes_by_location(processes)
    locations = list(loc_writes.keys())
    
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
            # print(r, w)
            if str(w.location) != str(r.location):
                # print("diff location")
                continue
            # Read value constrained
            if r.value != "None":
                # print("constrained")
                if str(w.value) == str(r.value):
                    # print("constrained correct")
                    candidates[r].append(w)
            # Read value unconstrained
            else:
                # print("candidate found")
                candidates[r].append(w)
                
    # print("rf_candidates", candidates)
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
            if e.type == "Write":
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

def enumerate_co_relations(processes):
    loc_writes = writes_by_location(processes)
    per_loc_orders = []

    for loc, writes in loc_writes.items():
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

parsed = parse_file("./litmus/ISA2+pooncerelease+poacquirerelease+poacquireonce.litmus")
# print(parsed)
converted = convert_to_events(parsed)
# print("converted =", converted)
apply_read_values(converted, parse_final_constraint("(r0=1 /\ r1=1 /\ r2=0)"))
rf = enumerate_rf_relations(converted, rf_candidates(converted)) # get final line from file?
# print("rf", rf)
co = enumerate_co_relations(converted)
# print("co", co)
fr = []
for rf_i in rf:
    for co_j in co:
        fr = generate_fr_relations(rf_i, co_j)
        # print("fr", fr)
        test = Execution(converted, rf_i, fr, co_j)
        print("hb", HappensBefore(test))
        print("pb", PropagatesBefore(test))
# test = Execution(converted, rf, fr, co) #[Relation(Event(1, "x", "Write", "Release", "C", 1, None), Event(2, "x", "Read", "Acquire", "C", 1, "r0")), Relation(Event(3, "y", "Write", "Relaxed", "C", 1, None), Event(0, "y", "Read", "Relaxed", "C", 1, "r1"))], [], [])

