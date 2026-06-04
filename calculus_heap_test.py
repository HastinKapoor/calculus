import networkx as nx
import re
import itertools
from collections import defaultdict
import copy
import argparse
from dataclasses import dataclass

from pprint import pprint

from parser_types import Operation, MemoryOrder, Language, Identifier, IterativeIdentifier, GlobalRegisters, Register
import os

class Event:
    # Identifier distinguishes two identical operations in different threads or thread positions
    def __init__(self, identifier: Identifier, location, type: Operation, strength: MemoryOrder, language: Language, value, register : Register):
        self.identifier = identifier
        self.location = location
        self.location_expr = location
        self.resolved_location = None
        self.type = type
        self.strength = strength
        self.language = language
        self.value = value
        self.value_expr = value
        self.register = register
    
    def __eq__(self, other):
        if not isinstance(self, Event) or not isinstance(other, Event):
            return type(self) == type(other) and self == other
        
        return self.identifier == other.identifier and self.location == other.location
    
    def __hash__(self):
        return hash(self.identifier)
    
    def __repr__(self):
        return f"Event({self.identifier}, {self.location}, {self.type}, {self.strength}, {self.language}, {self.value}, {self.register})"


@dataclass
class ParsedLitmus:
    threads: list[list[str]]
    init_statements: list[str]
    constraints: list[dict[str, object]]
    thread_params: list[list[str]]


def canonicalize_location_name(location):
    if location is None:
        return None

    location = str(location).strip()
    if location.startswith("[") and location.endswith("]"):
        location = location[1:-1].strip()
    if location.startswith("&"):
        location = location[1:].strip()
    if location.startswith("*"):
        location = location[1:].strip()
    if location.startswith("(") and location.endswith(")"):
        location = location[1:-1].strip()
    return location


def is_numeric_value(value):
    return isinstance(value, int) or (
        isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip())
    )


def normalize_constraint_value(raw):
    raw = raw.strip()
    if re.fullmatch(r"[+-]?\d+", raw):
        return int(raw)
    return canonicalize_location_name(raw)


def normalize_write_value(raw, name_to_location):
    if raw is None:
        return None

    if isinstance(raw, int):
        return raw

    raw = str(raw).strip()
    if raw == "None":
        return None
    if re.fullmatch(r"[+-]?\d+", raw):
        return int(raw)

    normalized = canonicalize_location_name(raw)
    if normalized in name_to_location:
        return name_to_location[normalized]
    return normalized


def values_equal(lhs, rhs):
    if lhs is None or rhs is None:
        return lhs is rhs

    if is_numeric_value(lhs) and is_numeric_value(rhs):
        return int(lhs) == int(rhs)

    return str(lhs) == str(rhs)


def EventLocation(event):
    if event is None:
        return None
    if event.resolved_location is not None:
        return event.resolved_location
    if event.location is None:
        return None
    return canonicalize_location_name(event.location)


def RegisterKey(register):
    if register is None:
        return None
    return register.id

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
    
    def SingleComposes(self, other):
        A = self
        B = other
        
        if A.Terminal() == B.Initial():
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

def AddUniqueRelation(result, relation):
    if relation not in result:
        result.append(relation)

def RelationToEndpoints(rel):
    return Relation(rel.Initial(), rel.Terminal())

def RelationToGraphEdges(rel):
    firsts = []
    lasts = []

    tmp = rel
    while isinstance(tmp, Relation):
        firsts.append(tmp.First())
        tmp = tmp.First()

    tmp = rel
    while isinstance(tmp, Relation):
        lasts.append(tmp.Last())
        tmp = tmp.Last()

    edges = []
    for s in firsts:
        for t in lasts:
            edges.append([s, t])

    for s in firsts:
        edges.append([s, rel])

    for s in lasts:
        edges.append([rel, s])

    return edges

def RelationsToGraphEdges(relations):
    edges = []
    for rel in relations:
        edges += RelationToGraphEdges(rel)
    return edges

def ToEventEdges(relations):
    edges = []
    for rel in relations:
        edge = [rel.Initial(), rel.Terminal()]
        if edge not in edges:
            edges.append(edge)
    return edges

def IsAcyclic(edges, nodes=None):
    G = nx.DiGraph()
    if nodes is not None:
        G.add_nodes_from(nodes)
    G.add_edges_from(edges)
    return nx.is_directed_acyclic_graph(G)

def ExecutionEvents(execution):
    result = []

    def add_event(event):
        if isinstance(event, Event) and event not in result:
            result.append(event)

    def visit(node):
        if isinstance(node, Event):
            add_event(node)
        elif isinstance(node, Relation):
            for element in node.elements:
                visit(element)

    for thread in execution.threads:
        for event in thread:
            add_event(event)

    for relation in execution.rf + execution.fr + execution.co + execution.ppo + execution.st + execution.prop:
        visit(relation)

    return result

def ToTransitiveClosureRelations(edges, nodes):
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    G.add_edges_from(edges)

    if not nx.is_directed_acyclic_graph(G):
        return None

    closure = []
    for node in nodes:
        closure.append(Relation(node, node))
        for descendant in nx.descendants(G, node):
            closure.append(Relation(node, descendant))

    return closure

def ToProgramOrder(threads):
    result = []

    for thread in threads:
        for i in range(len(thread)):
            for j in range(i + 1, len(thread)):
                result.append(Relation(thread[i], thread[j]))

    return result

def IsRCULock(event):
    return event.type == Operation.FENCE and event.strength == MemoryOrder.RCU_LOCK

def IsRCUUnlock(event):
    return event.type == Operation.FENCE and event.strength == MemoryOrder.RCU_UNLOCK

def IsSyncRCU(event):
    return event.type == Operation.FENCE and event.strength == MemoryOrder.SYNC_RCU

def IsRCUFence(event):
    return IsRCULock(event) or IsRCUUnlock(event) or IsSyncRCU(event)

# Cycle in communication + po-loc
def PerLocSC(threads, rf, fr, co):
    po_loc = []

    for thread in threads:
        for i in range(len(thread)):
            if EventLocation(thread[i]) is None:
                continue
            for j in range(i + 1, len(thread)):
                if EventLocation(thread[j]) is None:
                    continue
                if EventLocation(thread[i]) == EventLocation(thread[j]):
                    po_loc.append(Relation(thread[i], thread[j]))

    rel = po_loc + rf + fr + co
    edges = []

    for r in rel:
        edges.append([r.First(), r.Last()])

    G = nx.DiGraph(edges)
    return not nx.is_directed_acyclic_graph(G)

def NoThinAir(threads, rf):
    po = []

    for thread in threads:
        for i in range(len(thread)):
            for j in range(i + 1, len(thread)):
                po.append(Relation(thread[i], thread[j]))

    rel = [
        r
        for r in po + rf
        if r.First().language == Language.C and r.Last().language == Language.C
    ]
    edges = []

    for r in rel:
        edges.append([r.First(), r.Last()])

    G = nx.DiGraph(edges)
    return not nx.is_directed_acyclic_graph(G)

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
    strong_fence_strengths = {MemoryOrder.SEQ_CST, MemoryOrder.SYNC_RCU}
    for thread in threads:
        for i in range(len(thread)):
            if thread[i].type == Operation.FENCE and thread[i].strength in strong_fence_strengths:
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


def ToAddr(threads):
    result = []

    for thread in threads:
        register_to_read = {}
        for event in thread:
            expr = canonicalize_location_name(event.location_expr)
            if expr in register_to_read:
                result.append(Relation(register_to_read[expr], event))
            reg_key = RegisterKey(event.register)
            if reg_key is not None and event.type == Operation.READ:
                register_to_read[reg_key] = event

    return result

# Incomplete. There exist more PPOs than will be computed here, but these are "sufficient" for small litmus tests
# po-rel, acq-po, strong-fence, WMB, RMB
def ToPPO(threads):
    result = []
    
    result += (
        ToPoRel(threads)
        + ToAcqPo(threads)
        + ToStrongFence(threads)
        + ToWMB(threads)
        + ToRMB(threads)
        + ToAddr(threads)
    )
    
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

def ToHappensBeforeRelations(e):
    prop_int_nonempty = []
    for rel in e.prop:
        if InInt(e.threads, rel) and not InEmpty(rel):
            prop_int_nonempty.append(rel)

    return e.ppo + e.st + prop_int_nonempty

def ToHappensBefore(e):
    return RelationsToGraphEdges(ToHappensBeforeRelations(e))

def HappensBefore(e):
    edges = ToHappensBefore(e)
    # for e in edges:
    #     print(e)
    return not IsAcyclic(edges)

def ToPropagatesBeforeRelations(e):
    # print("PropagatesBefore called")
    strong_fences = ToStrongFence(e.threads)
    # for sf in strong_fences:
    #     print("Strong fence:", sf)

    rel = strong_fences.copy()

    for sf in strong_fences:
        for p in e.prop:
            if p.Composes(sf):
                # print(Relation(p, sf))
                AddUniqueRelation(rel, Relation(p, sf))

    return rel

def ToPropagatesBefore(e):
    rel = ToPropagatesBeforeRelations(e)
    return RelationsToGraphEdges(rel) + ToHappensBefore(e)

def PropagatesBefore(e):
    edges = ToPropagatesBefore(e)
    # for e in edges:
    #     print(e)
    return not IsAcyclic(edges)

def ToCompleteHappensBeforeRelations(e):
    return ToTransitiveClosureRelations(ToEventEdges(ToHappensBeforeRelations(e)), ExecutionEvents(e))

def ToCompletePropagatesBeforeRelations(e, hb_star=None):
    if hb_star is None:
        hb_star = ToCompleteHappensBeforeRelations(e)

    if hb_star is None:
        return None

    result = []
    strong_fences = [RelationToEndpoints(rel) for rel in ToStrongFence(e.threads)]
    prop_relations = [RelationToEndpoints(rel) for rel in e.prop]

    prop_to_strong_fence = []
    for prop in prop_relations:
        for strong_fence in strong_fences:
            if prop.SingleComposes(strong_fence):
                AddUniqueRelation(
                    prop_to_strong_fence,
                    Relation(prop.Initial(), strong_fence.Terminal()),
                )

    for rel in prop_to_strong_fence:
        AddUniqueRelation(result, rel)
        for hb in hb_star:
            if rel.SingleComposes(hb):
                AddUniqueRelation(result, Relation(rel.Initial(), hb.Terminal()))

    return result

def ToRCURSCSI(threads):
    result = []

    for thread in threads:
        stack = []
        for event in thread:
            if IsRCULock(event):
                stack.append(event)
            elif IsRCUUnlock(event) and stack:
                result.append(Relation(event, stack.pop()))

    return result

def ToRCUGP(threads):
    result = []

    for thread in threads:
        for event in thread:
            if IsSyncRCU(event):
                result.append(Relation(event, event))

    return result

def ToRCULink(e, hb_star, pb_star):
    result = []
    program_order = ToProgramOrder(e.threads)
    rcu_fence_events = [event for event in ExecutionEvents(e) if IsRCUFence(event)]
    start_po = [Relation(event, event) for event in rcu_fence_events]
    start_po += [rel for rel in program_order if IsRCUFence(rel.Initial())]
    end_po = [rel for rel in program_order if IsRCUFence(rel.Terminal())]
    prop_relations = [RelationToEndpoints(rel) for rel in e.prop]

    for po_start in start_po:
        for hb in hb_star:
            if not po_start.SingleComposes(hb):
                continue
            hb_progress = Relation(po_start.Initial(), hb.Terminal())
            for pb in pb_star:
                if not hb_progress.SingleComposes(pb):
                    continue
                pb_progress = Relation(hb_progress.Initial(), pb.Terminal())
                prop_progressions = [pb_progress]

                for prop in prop_relations:
                    if pb_progress.SingleComposes(prop):
                        prop_progressions.append(Relation(pb_progress.Initial(), prop.Terminal()))

                for prop_progress in prop_progressions:
                    for po_end in end_po:
                        if prop_progress.SingleComposes(po_end):
                            AddUniqueRelation(result, Relation(po_start.Initial(), po_end.Terminal()))

    return result

def ToRCUOrder(rcu_gp, rcu_rscsi, rcu_link):
    result = []

    for relation in rcu_gp:
        AddUniqueRelation(result, relation)

    changed = True
    while changed:
        changed = False
        additions = []

        for gp in rcu_gp:
            for link in rcu_link:
                if not gp.SingleComposes(link):
                    continue
                for rscsi in rcu_rscsi:
                    if link.SingleComposes(rscsi):
                        AddUniqueRelation(additions, Relation(gp.Initial(), rscsi.Terminal()))

        for rscsi in rcu_rscsi:
            for link in rcu_link:
                if not rscsi.SingleComposes(link):
                    continue
                for gp in rcu_gp:
                    if link.SingleComposes(gp):
                        AddUniqueRelation(additions, Relation(rscsi.Initial(), gp.Terminal()))

        for gp in rcu_gp:
            for link_left in rcu_link:
                if not gp.SingleComposes(link_left):
                    continue
                for order in result:
                    if not link_left.SingleComposes(order):
                        continue
                    for link_right in rcu_link:
                        if not order.SingleComposes(link_right):
                            continue
                        for rscsi in rcu_rscsi:
                            if link_right.SingleComposes(rscsi):
                                AddUniqueRelation(additions, Relation(gp.Initial(), rscsi.Terminal()))

        for rscsi in rcu_rscsi:
            for link_left in rcu_link:
                if not rscsi.SingleComposes(link_left):
                    continue
                for order in result:
                    if not link_left.SingleComposes(order):
                        continue
                    for link_right in rcu_link:
                        if not order.SingleComposes(link_right):
                            continue
                        for gp in rcu_gp:
                            if link_right.SingleComposes(gp):
                                AddUniqueRelation(additions, Relation(rscsi.Initial(), gp.Terminal()))

        for left in result:
            for link in rcu_link:
                if not left.SingleComposes(link):
                    continue
                for right in result:
                    if link.SingleComposes(right):
                        AddUniqueRelation(additions, Relation(left.Initial(), right.Terminal()))

        for relation in additions:
            if relation not in result:
                result.append(relation)
                changed = True

    return result

def ToRCUFence(e, rcu_order):
    result = []
    program_order = ToProgramOrder(e.threads)
    pre_po = [rel for rel in program_order if IsRCUFence(rel.Terminal())]
    post_po_optional = [Relation(event, event) for event in ExecutionEvents(e) if IsRCUFence(event)]
    post_po_optional += [rel for rel in program_order if IsRCUFence(rel.Initial())]

    for before in pre_po:
        for order in rcu_order:
            if not before.SingleComposes(order):
                continue
            for after in post_po_optional:
                if order.SingleComposes(after):
                    AddUniqueRelation(result, Relation(before.Initial(), after.Terminal()))

    return result

def ToRCUBefore(e):
    if HappensBefore(e) or PropagatesBefore(e):
        return []

    hb_star = ToCompleteHappensBeforeRelations(e)
    pb = ToCompletePropagatesBeforeRelations(e, hb_star)
    if hb_star is None or pb is None:
        return []

    pb_star = ToTransitiveClosureRelations(ToEventEdges(pb), ExecutionEvents(e))
    if pb_star is None:
        return []

    rcu_link = ToRCULink(e, hb_star, pb_star)
    rcu_order = ToRCUOrder(ToRCUGP(e.threads), ToRCURSCSI(e.threads), rcu_link)
    rcu_fence = ToRCUFence(e, rcu_order)
    prop_relations = [RelationToEndpoints(rel) for rel in e.prop]

    result = []
    for prop in prop_relations:
        for fence in rcu_fence:
            if not prop.SingleComposes(fence):
                continue
            prop_fence = Relation(prop.Initial(), fence.Terminal())
            for hb in hb_star:
                if not prop_fence.SingleComposes(hb):
                    continue
                prop_fence_hb = Relation(prop.Initial(), hb.Terminal())
                for pb_rel in pb_star:
                    if prop_fence_hb.SingleComposes(pb_rel):
                        AddUniqueRelation(
                            result,
                            Relation(prop_fence_hb.Initial(), pb_rel.Terminal()),
                        )

    return result

def RCU(e):
    edges = ToEventEdges(ToRCUBefore(e))
    return not IsAcyclic(edges, ExecutionEvents(e))

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
    def normalize_arg(arg):
        if arg == "None":
            return None
        return arg

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
        location = normalize_arg(args[0])
    if len(args) >= 2:
        value = normalize_arg(args[1])
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
        register_name = normalize_arg(args[4])
        if register_name is not None:
            register = global_registers.newRegister(identifier.getThreadId(), register_name)

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
                condition_set[reg.strip()] = normalize_constraint_value(val)
                
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
                loc = EventLocation(e)
                if loc is not None:
                    loc_writes[loc].append(e)

    return loc_writes

def canonicalize_location(location):
    return canonicalize_location_name(location)

def extract_location_constraints(constraints, inits, processes):
    known_locations = set()

    for init in inits:
        known_locations.add(canonicalize_location(EventLocation(init)))

    for process in processes:
        for event in process:
            loc = EventLocation(event)
            if loc is not None:
                known_locations.add(canonicalize_location(loc))

    location_constraints = []

    for clause in constraints:
        clause_locations = {}
        for key, val in clause.items():
            normalized_key = canonicalize_location(key)
            if normalized_key in known_locations:
                clause_locations[normalized_key] = val
        location_constraints.append(clause_locations)

    return location_constraints

def co_satisfies_constraints(selection, constraints, inits, processes):
    location_constraints = extract_location_constraints(constraints, inits, processes)
    location_finals = {
        canonicalize_location(EventLocation(init)): init.value
        for init in inits
    }

    for order in selection:
        if order:
            location_finals[canonicalize_location(EventLocation(order[0]))] = order[-1].value

    constrained_clauses = [clause for clause in location_constraints if clause]
    if not constrained_clauses:
        return True

    for clause in constrained_clauses:
        if all(str(location_finals.get(loc)) == str(val) for loc, val in clause.items()):
            return True

    return False

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

def enumerate_co_relations(processes, inits, constraints=None):
    loc_writes = writes_by_location(processes)
    per_loc_orders = []
    
    def find_init_for_loc(loc):
        for init in inits:
            if EventLocation(init) == loc:
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
        if constraints and not co_satisfies_constraints(selection, constraints, inits, processes):
            continue
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
    parts = [
        "None" if event.location is None else str(event.location),
        "None" if event.value is None else str(event.value),
    ]

    if event.strength:
        parts.append(event.strength.name)

    if event.language:
        parts.append(event.language.name)

    parts.append("None" if event.register is None else event.register.id)
    
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
                if event.location is None:
                    continue
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


def parse_heap_file(filename):
    threads = []
    thread_params = []
    current_thread = None
    init_statements = []
    constraints = []

    with open(filename, "r") as handle:
        for raw_line in handle:
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
                continue

            if current_thread is not None:
                current_thread.append(line)
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


def register_from_name(identifier, register_name):
    if register_name is None or register_name == "None":
        return None
    return global_registers.newRegister(identifier.getThreadId(), register_name)


def parse_heap_event(line, identifier):
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
            return Event(
                identifier,
                args[0].strip(),
                Operation.READ,
                MemoryOrder.ACQUIRE,
                Language.LINUX,
                None,
                register_from_name(identifier, register_name),
            )

        args = parse_macro_call(rhs, "READ_ONCE")
        if args and len(args) >= 1:
            return Event(
                identifier,
                args[0].strip(),
                Operation.READ,
                MemoryOrder.RELAXED,
                Language.LINUX,
                None,
                register_from_name(identifier, register_name),
            )

        args = parse_macro_call(rhs, "smp_load_acquire")
        if args and len(args) >= 1:
            return Event(
                identifier,
                args[0].strip(),
                Operation.READ,
                MemoryOrder.ACQUIRE,
                Language.LINUX,
                None,
                register_from_name(identifier, register_name),
            )

    args = parse_macro_call(stripped, "rcu_assign_pointer")
    if args and len(args) >= 2:
        return Event(
            identifier,
            args[0].strip(),
            Operation.WRITE,
            MemoryOrder.RELEASE,
            Language.LINUX,
            args[1].strip(),
            None,
        )

    args = parse_macro_call(stripped, "WRITE_ONCE")
    if args and len(args) >= 2:
        return Event(
            identifier,
            args[0].strip(),
            Operation.WRITE,
            MemoryOrder.RELAXED,
            Language.LINUX,
            args[1].strip(),
            None,
        )

    args = parse_macro_call(stripped, "smp_store_release")
    if args and len(args) >= 2:
        return Event(
            identifier,
            args[0].strip(),
            Operation.WRITE,
            MemoryOrder.RELEASE,
            Language.LINUX,
            args[1].strip(),
            None,
        )

    if parse_macro_call(stripped, "synchronize_rcu") is not None:
        return Event(identifier, None, Operation.FENCE, MemoryOrder.SYNC_RCU, Language.LINUX, None, None)

    if parse_macro_call(stripped, "rcu_read_lock") is not None:
        return Event(identifier, None, Operation.FENCE, MemoryOrder.RCU_LOCK, Language.LINUX, None, None)

    if parse_macro_call(stripped, "rcu_read_unlock") is not None:
        return Event(identifier, None, Operation.FENCE, MemoryOrder.RCU_UNLOCK, Language.LINUX, None, None)

    op_name, args = parse_operation_call(stripped)
    if op_name is None:
        raise ValueError(f"Cannot parse line: {line}")

    try:
        event_type = Operation[op_name.upper()]
    except KeyError as exc:
        raise ValueError(f"{op_name} is not a supported Operation") from exc

    location = args[0].strip() if len(args) >= 1 and args[0] != "None" else None
    value = args[1].strip() if len(args) >= 2 and args[1] != "None" else None
    strength = MemoryOrder.RELAXED
    language = Language.C
    register = None

    if len(args) >= 3:
        strength = MemoryOrder[args[2].strip().upper()]
    if len(args) >= 4:
        language = Language[args[3].strip().upper()]
    if len(args) >= 5:
        register = register_from_name(identifier, args[4].strip())

    return Event(identifier, location, event_type, strength, language, value, register)


def convert_to_heap_events(parsed_threads):
    events = []

    for thread_id, thread in enumerate(parsed_threads):
        iterator = IterativeIdentifier(thread_id)
        thread_events = []
        for line in thread:
            thread_events.append(parse_heap_event(line, iterator.next_id()))
        events.append(thread_events)

    return events


def collect_known_names(thread_params, threads, init_statements):
    known_names = set()

    for params in thread_params:
        known_names.update(params)

    for statement in init_statements:
        names = re.findall(r"[A-Za-z_]\w*", statement)
        known_names.update(names)

    for thread in threads:
        for event in thread:
            if event.location_expr is not None:
                known_names.add(canonicalize_location_name(event.location_expr))
            if isinstance(event.value, str):
                known_names.add(canonicalize_location_name(event.value))

    return {name for name in known_names if name is not None}


def is_simple_symbol(value):
    return bool(re.fullmatch(r"[A-Za-z_]\w*", value.strip()))


def build_name_resolution_and_inits(init_statements, thread_params, template_threads):
    known_names = collect_known_names(thread_params, template_threads, init_statements)
    name_to_location = {name: name for name in known_names}
    thread_param_names = {name for params in thread_params for name in params}

    alias_statements = []
    init_write_specs = []

    for statement in init_statements:
        if "=" not in statement:
            continue
        lhs_raw, rhs_raw = statement.split("=", 1)
        lhs_raw = lhs_raw.strip()
        rhs_raw = rhs_raw.strip()
        lhs_name = canonicalize_location_name(re.findall(r"[A-Za-z_]\w*", lhs_raw)[-1])

        is_alias = (
            lhs_name in thread_param_names
            and is_simple_symbol(rhs_raw)
            and "*" not in lhs_raw
            and "&" not in rhs_raw
            and not re.fullmatch(r"[+-]?\d+", rhs_raw)
        )

        if is_alias:
            alias_statements.append((lhs_name, canonicalize_location_name(rhs_raw)))
        else:
            init_write_specs.append((lhs_name, rhs_raw))

    for lhs_name, rhs_name in alias_statements:
        name_to_location[lhs_name] = name_to_location.get(rhs_name, rhs_name)

    init_iterator = IterativeIdentifier(thread_id=100)
    init_events = []
    for lhs_name, rhs_raw in init_write_specs:
        init_event = Event(
            init_iterator.next_id(),
            name_to_location.get(lhs_name, lhs_name),
            Operation.WRITE,
            MemoryOrder.INITIAL,
            Language.C,
            normalize_write_value(rhs_raw, name_to_location),
            None,
        )
        init_event.resolved_location = init_event.location
        init_events.append(init_event)

    return name_to_location, init_events


def ensure_initialized_locations(template_threads, init_events, name_to_location):
    known_locations = {EventLocation(init) for init in init_events}
    thread_registers = register_ids_by_thread(template_threads)

    required_locations = set()
    for thread in template_threads:
        for event in thread:
            resolved = resolve_location_expr(
                event.location_expr,
                {},
                thread_registers[event.identifier.getThreadId()],
                name_to_location,
            )
            if resolved is not None:
                required_locations.add(resolved)
            if isinstance(event.value, str):
                maybe_location = normalize_write_value(event.value, name_to_location)
                if isinstance(maybe_location, str) and maybe_location in name_to_location.values():
                    required_locations.add(maybe_location)

    init_iterator = IterativeIdentifier(thread_id=100)
    for _ in init_events:
        init_iterator.next_id()

    for location in sorted(required_locations):
        if location in known_locations:
            continue
        init_event = Event(
            init_iterator.next_id(),
            location,
            Operation.WRITE,
            MemoryOrder.INITIAL,
            Language.C,
            0,
            None,
        )
        init_event.resolved_location = location
        init_events.append(init_event)
        known_locations.add(location)


def register_ids_by_thread(threads):
    result = defaultdict(set)
    for thread in threads:
        for event in thread:
            reg_key = RegisterKey(event.register)
            if reg_key is not None:
                result[event.identifier.getThreadId()].add(reg_key)
    return result


def resolve_runtime_value(value, registers, name_to_location):
    if value is None or isinstance(value, int):
        return value

    token = str(value).strip()
    if token == "None":
        return None
    if re.fullmatch(r"[+-]?\d+", token):
        return int(token)
    if token in registers:
        return registers[token]
    return normalize_write_value(token, name_to_location)


def resolve_location_expr(expr, registers, known_registers, name_to_location):
    if expr is None:
        return None

    token = canonicalize_location_name(expr)
    if token in registers:
        location = registers[token]
        if is_numeric_value(location):
            return None
        return canonicalize_location_name(location)
    if token in known_registers:
        return None
    return name_to_location.get(token, token)


def resolve_direct_event_metadata(threads, init_events, name_to_location):
    thread_registers = register_ids_by_thread(threads)

    for init in init_events:
        init.resolved_location = canonicalize_location_name(init.location)
        init.value = normalize_write_value(init.value_expr, name_to_location)

    for thread in threads:
        for event in thread:
            known_registers = thread_registers[event.identifier.getThreadId()]
            event.resolved_location = resolve_location_expr(
                event.location_expr,
                {},
                known_registers,
                name_to_location,
            )
            if event.type == Operation.WRITE:
                event.value = normalize_write_value(event.value_expr, name_to_location)


def refresh_dynamic_write_metadata(threads, init_events, registers, thread_registers, name_to_location):
    for init in init_events:
        init.resolved_location = canonicalize_location_name(init.location)
        init.value = normalize_write_value(init.value_expr, name_to_location)

    for thread in threads:
        for event in thread:
            if event.type != Operation.WRITE:
                continue

            thread_id = event.identifier.getThreadId()
            event.resolved_location = resolve_location_expr(
                event.location_expr,
                registers[thread_id],
                thread_registers[thread_id],
                name_to_location,
            )
            event.value = resolve_runtime_value(
                event.value_expr,
                registers[thread_id],
                name_to_location,
            )


def enumerate_rf_executions(template_threads, template_inits, name_to_location):
    threads = copy.deepcopy(template_threads)
    init_events = copy.deepcopy(template_inits)
    resolve_direct_event_metadata(threads, init_events, name_to_location)

    thread_registers = register_ids_by_thread(threads)
    reads = []
    writes = list(init_events)

    for thread in threads:
        for event in thread:
            if event.type == Operation.READ:
                reads.append(event)
            elif event.type == Operation.WRITE:
                writes.append(event)

    registers = defaultdict(dict)
    rf = []
    results = []

    def backtrack(index):
        refresh_dynamic_write_metadata(
            threads,
            init_events,
            registers,
            thread_registers,
            name_to_location,
        )

        if index >= len(reads):
            results.append(copy.deepcopy((threads, init_events, rf)))
            return

        read_event = reads[index]
        thread_id = read_event.identifier.getThreadId()
        reg_key = RegisterKey(read_event.register)
        resolved_location = resolve_location_expr(
            read_event.location_expr,
            registers[thread_id],
            thread_registers[thread_id],
            name_to_location,
        )
        if resolved_location is None:
            return

        previous_location = read_event.resolved_location
        previous_value = read_event.value
        read_event.resolved_location = resolved_location

        candidates = []
        for write_event in writes:
            if EventLocation(write_event) is None:
                continue
            if EventLocation(write_event) != resolved_location:
                continue
            write_value = resolve_runtime_value(
                write_event.value,
                registers[write_event.identifier.getThreadId()],
                name_to_location,
            )
            if previous_value is not None and not values_equal(previous_value, write_value):
                continue
            candidates.append((write_event, write_value))

        for write_event, write_value in candidates:
            previous_register_value = registers[thread_id].get(reg_key) if reg_key is not None else None
            previous_present = reg_key in registers[thread_id] if reg_key is not None else False

            read_event.value = write_value
            if reg_key is not None:
                registers[thread_id][reg_key] = write_value
            rf.append(Relation(write_event, read_event))
            backtrack(index + 1)
            rf.pop()

            if reg_key is not None:
                if previous_present:
                    registers[thread_id][reg_key] = previous_register_value
                else:
                    registers[thread_id].pop(reg_key, None)

        read_event.value = previous_value
        read_event.resolved_location = previous_location

    backtrack(0)
    return results


def final_write_by_location(threads, init_events, co):
    writes = defaultdict(list)
    outgoing = defaultdict(set)

    for init in init_events:
        writes[EventLocation(init)].append(init)

    for thread in threads:
        for event in thread:
            if event.type == Operation.WRITE:
                writes[EventLocation(event)].append(event)

    for rel in co:
        outgoing[rel.First()].add(rel.Last())

    finals = {}
    for location, location_writes in writes.items():
        final_write = None
        for write_event in location_writes:
            if write_event not in outgoing:
                final_write = write_event
        if final_write is None and location_writes:
            final_write = location_writes[-1]
        finals[location] = final_write

    return finals


def constraints_satisfied(threads, init_events, co, constraints, name_to_location):
    if not constraints:
        return True

    register_values = {}
    for thread in threads:
        for event in thread:
            reg_key = RegisterKey(event.register)
            if reg_key is not None:
                register_values[reg_key] = event.value

    final_writes = final_write_by_location(threads, init_events, co)
    final_locations = {
        location: write_event.value for location, write_event in final_writes.items()
    }

    for clause in constraints:
        matched = True
        for key, expected in clause.items():
            if key in register_values:
                actual = register_values[key]
            else:
                actual = final_locations.get(name_to_location.get(canonicalize_location_name(key), canonicalize_location_name(key)))
            if not values_equal(actual, expected):
                matched = False
                break
        if matched:
            return True

    return False

    

# print("============================================")

# Allow specifying the litmus input file on the command line (positional, optional)
parser = argparse.ArgumentParser(description="Run a litmus test file")
parser.add_argument('input', nargs='?', default='input_test.litmus', help='Path to litmus test file')
args = parser.parse_args()
input_filename = args.input

parsed_litmus = parse_heap_file(input_filename)

global_registers = GlobalRegisters(len(parsed_litmus.threads))

converted = convert_to_heap_events(parsed_litmus.threads)
name_to_location, initialization_events = build_name_resolution_and_inits(
    parsed_litmus.init_statements,
    parsed_litmus.thread_params,
    converted,
)
ensure_initialized_locations(converted, initialization_events, name_to_location)

result = "Forbidden"
rf_executions = enumerate_rf_executions(converted, initialization_events, name_to_location)

for concrete_threads, concrete_inits, rf_i in rf_executions:
    co_candidates = enumerate_co_relations(concrete_threads, concrete_inits)
    for co_j in co_candidates:
        if not constraints_satisfied(
            concrete_threads,
            concrete_inits,
            co_j,
            parsed_litmus.constraints,
            name_to_location,
        ):
            continue
        fr = generate_fr_relations(rf_i, co_j)
        test = Execution(concrete_threads, rf_i, fr, co_j)
        if (
            not NoThinAir(concrete_threads, rf_i)
            and not PerLocSC(concrete_threads, rf_i, fr, co_j)
            and not HappensBefore(test)
            and not PropagatesBefore(test)
            and not RCU(test)
        ):
           result = "Allowed"
           break
    if result == "Allowed":
        break
print(f"{input_filename}: {result}")
# test = Execution(converted, rf, fr, co) #[Relation(Event(1, "x", "Write", "Release", "C", 1, None), Event(2, "x", "Read", "Acquire", "C", 1, "r0")), Relation(Event(3, "y", "Write", "Relaxed", "C", 1, None), Event(0, "y", "Read", "Relaxed", "C", 1, "r1"))], [], [])
