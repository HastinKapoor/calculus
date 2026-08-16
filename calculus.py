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
    def __init__(self, identifier: Identifier, location, type: Operation, strength: MemoryOrder, language: Language, value: int | None, register : Register, guards=None, raw_location=None, raw_value=None):
        self.identifier = identifier
        self.location = location
        self.raw_location = location if raw_location is None else raw_location
        self.type = type
        self.strength = strength
        self.language = language
        self.value = value
        self.raw_value = value if raw_value is None else raw_value
        self.register = register
        self.guards = [] if guards is None else list(guards)
    
    def __eq__(self, other):
        if not isinstance(self, Event) or not isinstance(other, Event):
            return type(self) == type(other) and self == other
        
        return self.identifier == other.identifier and self.location == other.location
    
    def __hash__(self):
        return hash(self.identifier)
    
    def __repr__(self):
        return f"Event({self.identifier}, {self.location}, {self.type}, {self.strength}, {self.language}, {self.value}, {self.register})"


class GuardCondition:
    def __init__(self, condition: str, expected: bool = True):
        self.condition = condition
        self.expected = expected

    def __repr__(self):
        return f"GuardCondition({self.condition!r}, expected={self.expected})"


class GuardedLine:
    def __init__(self, line: str, guards=None):
        self.line = line
        self.guards = [] if guards is None else list(guards)

    def __repr__(self):
        return f"GuardedLine({self.line!r}, guards={self.guards!r})"

class Relation:
    def __init__(self, *args):
        self.elements = []
        for arg in args:
            if isinstance(arg, Relation):
                self.elements.extend(arg.elements)
            else:
                self.elements.append(arg)

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
        return hash(tuple(self.elements))
    
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

def FlattenRelationElements(rel):
    flattened = []

    for element in rel.elements:
        if isinstance(element, Relation):
            flattened.extend(FlattenRelationElements(element))
        else:
            flattened.append(element)

    return flattened

def ComposeFlattenedRelation(left, right):
    left_elements = FlattenRelationElements(left)
    right_elements = FlattenRelationElements(right)
    max_overlap = min(len(left_elements), len(right_elements))

    for overlap in range(max_overlap, 0, -1):
        if left_elements[-overlap:] == right_elements[:overlap]:
            return Relation(*(left_elements + right_elements[overlap:]))

    return None

def CloseTransitiveCompositions(relations):
    result = relations.copy()
    changed = True

    while changed:
        changed = False
        snapshot = result.copy()
        for left in snapshot:
            for right in snapshot:
                composed = ComposeFlattenedRelation(left, right)
                if composed is not None:
                    if composed not in result:
                        result.append(composed)
                        changed = True

    return result

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

def IsLockRead(event):
    return event.type == Operation.READ and event.strength == MemoryOrder.LOCK_READ

def IsLockWrite(event):
    return event.type == Operation.WRITE and event.strength == MemoryOrder.LOCK_WRITE

def IsUnlock(event):
    return event.type == Operation.WRITE and event.strength == MemoryOrder.UNLOCK

def IsSyncRCU(event):
    return event.type == Operation.FENCE and event.strength == MemoryOrder.SYNC_RCU

def IsRCUFence(event):
    return IsRCULock(event) or IsRCUUnlock(event) or IsSyncRCU(event)


def GuardMentionsRegister(condition, register):
    if register is None:
        return False

    return re.search(r"\b" + re.escape(register.id) + r"\b", condition) is not None


def raw_value_mentions_register(raw_value, register):
    if register is None or raw_value is None or not isinstance(raw_value, str):
        return False

    return re.search(r"\b" + re.escape(register.id) + r"\b", raw_value) is not None


def build_carried_dependency_state(thread):
    carried_sources = {}
    register_sources = {}
    latest_write_by_location = {}

    for event in thread:
        sources = set()

        if event.type == Operation.READ and event.register is not None:
            if event.language == Language.LINUX:
                sources.add(event)

            if event.location is not None and not is_indirect_location(event.location):
                prior_write = latest_write_by_location.get(event.location)
                if prior_write is not None:
                    sources.update(carried_sources.get(prior_write, set()))

            carried_sources[event] = sources
            register_sources[event.register.id] = set(sources)
            continue

        if event.type == Operation.WRITE:
            for register_id, register_carried_sources in register_sources.items():
                if re.search(r"\b" + re.escape(register_id) + r"\b", str(event.raw_value)):
                    sources.update(register_carried_sources)

            carried_sources[event] = sources

            if event.location is not None and not is_indirect_location(event.location):
                latest_write_by_location[event.location] = event
            continue

        carried_sources[event] = sources

    return carried_sources, register_sources

# Cycle in communication + po-loc
def PerLocSC(threads, rf, fr, co):
    po_loc = []

    for thread in threads:
        for i in range(len(thread)):
            if thread[i].location is None:
                continue
            for j in range(i + 1, len(thread)):
                if thread[j].location is None:
                    continue
                if thread[i].location == thread[j].location:
                    po_loc.append(Relation(thread[i], thread[j]))

    rel = po_loc + rf + fr + co
    edges = []

    for r in rel:
        edges.append([r.First(), r.Last()])

    G = nx.DiGraph(edges)
    return not nx.is_directed_acyclic_graph(G)

def NoThinAir(threads, rf):
    sb = []

    for thread in threads:
        for i in range(len(thread) - 1):
            left = thread[i]
            right = thread[i + 1]
            if left.language == Language.C and right.language == Language.C:
                sb.append(Relation(left, right))

    rel = [
        r
        for r in sb + rf
        if r.First().language == Language.C and r.Last().language == Language.C
    ]
    edges = []

    for r in rel:
        edges.append([r.First(), r.Last()])

    G = nx.DiGraph(edges)
    return not nx.is_directed_acyclic_graph(G)

def ToRMWPairs(threads):
    result = []

    for thread in threads:
        for i in range(len(thread) - 1):
            left = thread[i]
            right = thread[i + 1]

            if (
                IsLockRead(left)
                and IsLockWrite(right)
                and left.location is not None
                and left.location == right.location
            ):
                AddUniqueRelation(result, Relation(left, right))
            elif left.type == Operation.RMW_R and right.type == Operation.RMW_W:
                AddUniqueRelation(result, Relation(left, right))

    return result

def Atomicity(threads, fr, co):
    rmw = ToRMWPairs(threads)

    fre = [
        rel for rel in fr
        if rel.First().identifier.getThreadId() != rel.Last().identifier.getThreadId()
    ]
    coe = [
        rel for rel in co
        if rel.First().identifier.getThreadId() != rel.Last().identifier.getThreadId()
    ]

    fre_coe = []
    for fr_rel in fre:
        for co_rel in coe:
            if fr_rel.SingleComposes(co_rel):
                AddUniqueRelation(
                    fre_coe,
                    Relation(fr_rel.Initial(), co_rel.Terminal()),
                )

    return any(rmw_rel in fre_coe for rmw_rel in rmw)

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
    strong_fence_strengths = {
        MemoryOrder.SEQ_CST,
        MemoryOrder.SYNC_RCU,
        MemoryOrder.AFTER_SPINLOCK,
    }
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


def ToAddrDep(threads):
    result = []

    for thread in threads:
        _, register_sources = build_carried_dependency_state(thread)
        for i in range(len(thread)):
            src = thread[i]
            if src.language != Language.LINUX or src.type != Operation.READ or src.register is None:
                continue

            for j in range(i + 1, len(thread)):
                dst = thread[j]
                if dst.language != Language.LINUX:
                    continue

                if (
                    is_indirect_location(dst.raw_location)
                    and dst.raw_location[1:] == src.register.id
                ):
                    AddUniqueRelation(result, Relation(src, dst))

        for j in range(len(thread)):
            dst = thread[j]
            if dst.language != Language.LINUX or not is_indirect_location(dst.raw_location):
                continue

            carried_reads = register_sources.get(dst.raw_location[1:], set())
            for src in carried_reads:
                if src.language == Language.LINUX and src.type == Operation.READ:
                    AddUniqueRelation(result, Relation(src, dst))

    return result


def ToDataDep(threads):
    result = []

    for thread in threads:
        carried_sources, register_sources = build_carried_dependency_state(thread)
        for i in range(len(thread)):
            src = thread[i]
            if src.language != Language.LINUX or src.type != Operation.READ or src.register is None:
                continue

            for j in range(i + 1, len(thread)):
                dst = thread[j]
                if dst.language != Language.LINUX or dst.type != Operation.WRITE:
                    continue

                if raw_value_mentions_register(dst.raw_value, src.register):
                    AddUniqueRelation(result, Relation(src, dst))

        for j in range(len(thread)):
            dst = thread[j]
            if dst.language != Language.LINUX or dst.type != Operation.WRITE:
                continue

            for register_id in re.findall(r"[A-Za-z_]\\w*", str(dst.raw_value)):
                for src in register_sources.get(register_id, set()):
                    if src.language == Language.LINUX and src.type == Operation.READ:
                        AddUniqueRelation(result, Relation(src, dst))

                for prior_event, prior_sources in carried_sources.items():
                    if (
                        prior_event.identifier.getThreadId() == dst.identifier.getThreadId()
                        and prior_event.identifier.event_id < dst.identifier.event_id
                        and isinstance(prior_event.register, Register)
                        and prior_event.register.id == register_id
                    ):
                        for src in prior_sources:
                            if src.language == Language.LINUX and src.type == Operation.READ:
                                AddUniqueRelation(result, Relation(src, dst))

    return result


def ToCtrlDep(threads):
    result = []

    for thread in threads:
        _, register_sources = build_carried_dependency_state(thread)
        for i in range(len(thread)):
            src = thread[i]
            if src.language != Language.LINUX or src.type != Operation.READ or src.register is None:
                continue

            for j in range(i + 1, len(thread)):
                dst = thread[j]
                if dst.language != Language.LINUX or dst.type != Operation.WRITE:
                    continue

                if any(GuardMentionsRegister(guard.condition, src.register) for guard in dst.guards):
                    AddUniqueRelation(result, Relation(src, dst))

        for j in range(len(thread)):
            dst = thread[j]
            if dst.language != Language.LINUX or dst.type != Operation.WRITE:
                continue

            for guard in dst.guards:
                for register_id in re.findall(r"[A-Za-z_]\w*", guard.condition):
                    for src in register_sources.get(register_id, set()):
                        if src.language == Language.LINUX and src.type == Operation.READ:
                            AddUniqueRelation(result, Relation(src, dst))

    return result

def ToPoUnlockLockPo(threads, rf=None, internal_only=False):
    result = []
    rf = [] if rf is None else rf

    for unlock_thread in threads:
        for unlock_idx, unlock_event in enumerate(unlock_thread):
            if not IsUnlock(unlock_event):
                continue

            for lock_thread in threads:
                if internal_only and lock_thread is not unlock_thread:
                    continue

                for lock_idx, lock_event in enumerate(lock_thread):
                    if not IsLockRead(lock_event):
                        continue

                    same_thread_handoff = lock_thread is unlock_thread and unlock_idx < lock_idx
                    rf_handoff = any(
                        rel.Initial() == unlock_event and rel.Terminal() == lock_event
                        for rel in rf
                    )

                    if internal_only:
                        if not same_thread_handoff:
                            continue
                    elif not (same_thread_handoff or rf_handoff):
                        continue

                    for start_idx in range(unlock_idx):
                        start_event = unlock_thread[start_idx]
                        if start_event.type == Operation.FENCE:
                            continue

                        for end_idx in range(lock_idx + 1, len(lock_thread)):
                            end_event = lock_thread[end_idx]
                            if end_event.type == Operation.FENCE:
                                continue

                            AddUniqueRelation(
                                result,
                                Relation(start_event, unlock_event, lock_event, end_event),
                            )

    return result

# Incomplete. There exist more PPOs than will be computed here, but these are "sufficient" for small litmus tests
# po-rel, acq-po, strong-fence, WMB, RMB, Linux addr/ctrl deps,
# po-unlock-lock-po & int
def ToPPO(threads):
    result = []
    
    result += (
        ToPoRel(threads)
        + ToAcqPo(threads)
        + ToStrongFence(threads)
        + ToWMB(threads)
        + ToRMB(threads)
        + ToAddrDep(threads)
        + ToDataDep(threads)
        + ToCtrlDep(threads)
        + ToPoUnlockLockPo(threads, internal_only=True)
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
    
    # Here we add the cumulative fences needed for release/acquire,
    # WMB propagation, and lock handoff propagation.
    po_rel = ToPoRel(threads)
    strong_fence = ToStrongFence(threads)
    po_unlock_lock_po = ToPoUnlockLockPo(threads, rf=rf, internal_only=False)
    cumul_fences = po_rel.copy() + strong_fence + ToWMB(threads) + po_unlock_lock_po
    for r in synct:
        for pr in po_rel:
            if r.Composes(pr):
                cumul_fences.append(Relation(r, pr))
    cumul_fences = CloseTransitiveCompositions(cumul_fences)
    
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

def ToRCUFenceSuffixTerminals(fence_terminal, hb_star, pb_star):
    hb_terminals = [fence_terminal]

    for hb in hb_star:
        if hb.Initial() == fence_terminal and hb.Terminal() not in hb_terminals:
            hb_terminals.append(hb.Terminal())

    result = hb_terminals.copy()

    for hb_terminal in hb_terminals:
        for pb in pb_star:
            if pb.Initial() == hb_terminal and pb.Terminal() not in result:
                result.append(pb.Terminal())

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
            for terminal in ToRCUFenceSuffixTerminals(fence.Terminal(), hb_star, pb_star):
                AddUniqueRelation(result, Relation(prop.Initial(), terminal))

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
        for entry in thread:
            if isinstance(entry, GuardedLine):
                line = entry.line
                guards = entry.guards
            else:
                line = entry
                guards = []

            event = parse_event(line, threadIdIterator.next_id(), guards=guards)
            thread_events.append(event)
        events.append(thread_events)

    return events

def parse_event(line, identifier, guards=None):
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

    return Event(
        identifier,
        location,
        event_type,
        strength,
        language,
        value,
        register,
        guards=guards,
        raw_location=location,
        raw_value=value,
    )

def process_init_vals(line):
    def normalize_init_location(text):
        text = text.strip()
        text = re.sub(r"^\s*(?:const\s+|volatile\s+|atomic_\w+\s+)*", "", text)
        match = re.search(r"([A-Za-z_]\w*)\s*$", text)
        if match:
            return match.group(1)
        return text

    def normalize_init_value(text):
        text = text.strip()

        if re.fullmatch(r"-?\d+", text):
            return int(text)

        if text.startswith("&"):
            return normalize_init_location(text[1:])

        return text

    # get rid of the braces
    line = line.replace("{", "").replace("}", "").strip()
    # split into individual inits
    inits = line.split(";")
    
    inits = [_ for _ in inits if _.strip()]
    
    initializations = []
    
    initIdIterator = IterativeIdentifier(thread_id=100)
    
    for init in inits:
        if "=" not in init:
            raise ValueError(f"Cannot parse initialization: {init}")

        loc, val = init.split("=", 1)
        parsed_loc = normalize_init_location(loc)
        parsed_value = normalize_init_value(val)

        initializations.append(
            Event(
                initIdIterator.next_id(),
                parsed_loc,
                Operation.WRITE,
                MemoryOrder.INITIAL,
                Language.C,
                parsed_value,
                None,
            )
        )
    
    return initializations

def split_control_flow_line(line):
    stripped = line.strip()

    if re.fullmatch(r"}\s*else\s*{", stripped):
        return ["}", "else {"]

    return [stripped]

def tokenize_thread_lines(thread_lines):
    tokens = []

    for line in thread_lines:
        tokens.extend(token for token in split_control_flow_line(line) if token)

    return tokens

def is_if_statement(line):
    return re.match(r"^if\b", line) is not None

def is_else_statement(line):
    return re.match(r"^else\b", line) is not None

def line_opens_block(line):
    return line.rstrip().endswith("{")

def has_wrapping_parentheses(text):
    if not (text.startswith("(") and text.endswith(")")):
        return False

    depth = 0
    for idx, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1

        if depth == 0 and idx != len(text) - 1:
            return False

    return depth == 0

def strip_wrapping_parentheses(text):
    text = text.strip()

    while has_wrapping_parentheses(text):
        text = text[1:-1].strip()

    return text

def extract_if_condition(line):
    remainder = line[len("if"):].strip()

    if remainder.endswith("{"):
        remainder = remainder[:-1].strip()

    return strip_wrapping_parentheses(remainder)

def resolve_condition_operand(token, read_values):
    token = strip_wrapping_parentheses(token.strip())

    if token in read_values:
        return read_values[token]

    if re.fullmatch(r"-?\d+", token):
        return int(token)

    return None

def evaluate_condition(condition, read_values):
    condition = strip_wrapping_parentheses(condition)

    comparison = re.match(r"^(.*?)\s*(==|!=)\s*(.*?)$", condition)
    if comparison:
        left = resolve_condition_operand(comparison.group(1), read_values)
        right = resolve_condition_operand(comparison.group(3), read_values)

        if left is None or right is None:
            return None

        if comparison.group(2) == "==":
            return left == right

        return left != right

    value = resolve_condition_operand(condition, read_values)
    if value is None:
        return None

    return bool(value)

def parse_control_flow_statement(tokens, index, read_values):
    line = tokens[index]

    if is_if_statement(line):
        return parse_if_statement(tokens, index, read_values)

    return [GuardedLine(line)], index + 1

def parse_if_statement(tokens, index, read_values):
    line = tokens[index]
    condition = extract_if_condition(line)
    index += 1

    if line_opens_block(line):
        then_branch, index = parse_control_flow_block(tokens, index, read_values, stop_on_close=True)
    else:
        then_branch, index = parse_control_flow_statement(tokens, index, read_values)

    else_branch = []
    if index < len(tokens) and is_else_statement(tokens[index]):
        else_line = tokens[index]
        index += 1

        if line_opens_block(else_line):
            else_branch, index = parse_control_flow_block(tokens, index, read_values, stop_on_close=True)
        else:
            else_branch, index = parse_control_flow_statement(tokens, index, read_values)

    guard = GuardCondition(condition, expected=True)
    negated_guard = GuardCondition(condition, expected=False)

    guarded_then = [
        GuardedLine(stmt.line, stmt.guards + [guard])
        for stmt in then_branch
    ]
    guarded_else = [
        GuardedLine(stmt.line, stmt.guards + [negated_guard])
        for stmt in else_branch
    ]

    return guarded_then + guarded_else, index

def parse_control_flow_block(tokens, index, read_values, stop_on_close=False):
    events = []

    while index < len(tokens):
        line = tokens[index]

        if line == "}":
            if stop_on_close:
                return events, index + 1

            index += 1
            continue

        if is_else_statement(line):
            return events, index

        statement_events, index = parse_control_flow_statement(tokens, index, read_values)
        events.extend(statement_events)

    return events, index

def resolve_thread_control_flow(thread_lines, read_values):
    tokens = tokenize_thread_lines(thread_lines)
    resolved, _ = parse_control_flow_block(tokens, 0, read_values)
    return resolved

def resolve_control_flow(parsed_threads, constraints):
    return [
        resolve_thread_control_flow(thread, {})
        for thread in parsed_threads
    ]
    
def parse_file(filename):
    threads = []
    current_thread = None
    current_thread_depth = 0
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
                current_thread_depth = 1

            # End of a thread
            elif line == "}" and current_thread is not None and current_thread_depth == 1:
                threads.append(current_thread)
                current_thread = None
                current_thread_depth = 0

            # Inside a thread: collect statements and keep nested control-flow braces.
            elif current_thread is not None and line:
                current_thread.append(line)
                current_thread_depth += line.count("{") - line.count("}")

                if current_thread_depth == 0:
                    threads.append(current_thread)
                    current_thread = None
                    current_thread_depth = 0

            elif line == "}":
                if current_thread is not None:
                    threads.append(current_thread)
                    current_thread = None
                
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
                val = val.strip()

                try:
                    parsed_value = int(val)
                except ValueError:
                    parsed_value = val

                condition_set[reg.strip()] = parsed_value
                
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


def is_indirect_location(location):
    return isinstance(location, str) and location.startswith("*")


def resolve_indirect_locations(threads, read_values):
    if not read_values:
        return

    value_map = read_values[0]
    changed = True
    while changed:
        changed = False
        for thread in threads:
            for event in thread:
                if not is_indirect_location(event.location):
                    continue
                target = event.location[1:]
                if target in value_map:
                    event.location = str(value_map[target])
                    changed = True


def resolve_indirect_locations_from_register_values(threads):
    value_map = build_register_value_map(threads)
    changed = True

    while changed:
        changed = False
        for thread in threads:
            for event in thread:
                if not is_indirect_location(event.raw_location):
                    continue

                target = event.raw_location[1:]
                if target not in value_map:
                    continue

                new_location = str(value_map[target])
                if event.location != new_location:
                    event.location = new_location
                    changed = True

        if changed:
            value_map = build_register_value_map(threads)


def build_register_value_map(threads):
    register_values = {}

    for thread in threads:
        for event in thread:
            if event.register is not None and event.value is not None:
                register_values[event.register.id] = event.value

    return register_values


def resolve_event_values_from_register_values(threads):
    value_map = build_register_value_map(threads)
    changed = True

    while changed:
        changed = False
        for thread in threads:
            for event in thread:
                if event.value is None or not isinstance(event.value, str):
                    continue

                token = event.value.strip()
                if token not in value_map:
                    continue

                new_value = value_map[token]
                if event.value != new_value:
                    event.value = new_value
                    changed = True

        if changed:
            value_map = build_register_value_map(threads)


def resolve_guard_operand(token, register_values):
    token = strip_wrapping_parentheses(token.strip())

    if token in register_values:
        value = register_values[token]
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
            return int(value)
        return value

    if re.fullmatch(r"-?\d+", token):
        return int(token)

    if token.startswith("*"):
        return None

    return token


def evaluate_guard_condition(condition, register_values):
    condition = strip_wrapping_parentheses(condition)

    comparison = re.match(r"^(.*?)\s*(==|!=)\s*(.*?)$", condition)
    if comparison:
        left = resolve_guard_operand(comparison.group(1), register_values)
        right = resolve_guard_operand(comparison.group(3), register_values)

        if left is None or right is None:
            return None

        if comparison.group(2) == "==":
            return left == right

        return left != right

    value = resolve_guard_operand(condition, register_values)
    if value is None:
        return None

    if isinstance(value, str) and condition not in register_values:
        return None

    return bool(value)


def EventGuardsSatisfied(event, register_values):
    for guard in event.guards:
        guard_result = evaluate_guard_condition(guard.condition, register_values)
        if guard_result is None:
            continue
        if guard_result != guard.expected:
            return False

    return True


def FilterActiveThreads(threads):
    register_values = build_register_value_map(threads)
    active_threads = []
    active_events = set()

    for thread in threads:
        active_thread = []
        for event in thread:
            if EventGuardsSatisfied(event, register_values):
                active_thread.append(event)
                active_events.add(event)
        active_threads.append(active_thread)

    return active_threads, active_events


def FilterRelationsToActiveEvents(relations, active_events):
    return [
        rel for rel in relations
        if rel.Last() in active_events and (
            rel.First() in active_events
            or rel.First().strength == MemoryOrder.INITIAL
        )
    ]


def RelationTerminalReads(relations):
    return {rel.Last() for rel in relations}


def CloneThreadsAndInits(threads, inits):
    cloned_threads = copy.deepcopy(threads)
    cloned_inits = copy.deepcopy(inits)
    event_map = {}

    for original_thread, cloned_thread in zip(threads, cloned_threads):
        for original_event, cloned_event in zip(original_thread, cloned_thread):
            event_map[original_event] = cloned_event

    for original_init, cloned_init in zip(inits, cloned_inits):
        event_map[original_init] = cloned_init

    return cloned_threads, cloned_inits, event_map


def RemapRelation(rel, event_map):
    remapped = []

    for element in rel.elements:
        if isinstance(element, Relation):
            remapped.append(RemapRelation(element, event_map))
        else:
            remapped.append(event_map[element])

    return Relation(*remapped)


def RemapRelations(relations, event_map):
    return [RemapRelation(rel, event_map) for rel in relations]


def rf_candidates_for_unassigned_reads(processes, inits, assigned_reads):
    loc_writes = writes_by_location(processes)
    writes = inits.copy()
    reads = []

    for process in processes:
        for event in process:
            if event.type == Operation.WRITE:
                writes.append(event)
            elif event.type == Operation.READ and event not in assigned_reads:
                reads.append(event)

    candidates = defaultdict(list)

    for read in reads:
        if is_indirect_location(read.location):
            continue

        for write in writes:
            if str(write.location) != str(read.location):
                continue

            if read.value is not None:
                if str(write.value) == str(read.value):
                    candidates[read].append(write)
            else:
                candidates[read].append(write)

    return candidates


def ActiveReads(threads):
    return [
        event
        for thread in threads
        for event in thread
        if event.type == Operation.READ
    ]


def EnumerateResolvedRFExecutions(threads, inits, rf):
    # Guarded control flow must be evaluated using the RF choices already made
    # for this execution prefix; otherwise C no-thin-air cycles such as arfna
    # can disappear before we filter to the active events.
    ApplyRFValues(threads, rf)
    resolve_event_values_from_register_values(threads)
    resolve_indirect_locations_from_register_values(threads)
    active_threads, active_events = FilterActiveThreads(threads)
    active_rf = FilterRelationsToActiveEvents(rf, active_events)

    assigned_reads = RelationTerminalReads(active_rf)
    remaining_reads = [read for read in ActiveReads(active_threads) if read not in assigned_reads]

    if not remaining_reads:
        yield active_threads, inits, active_rf
        return

    extra_candidates = rf_candidates_for_unassigned_reads(active_threads, inits, assigned_reads)

    for read in remaining_reads:
        if read not in extra_candidates or not extra_candidates[read]:
            return

    for extra_rf in enumerate_rf_relations(active_threads, extra_candidates):
        cloned_threads, cloned_inits, event_map = CloneThreadsAndInits(active_threads, inits)
        combined_rf = active_rf + extra_rf
        cloned_rf = RemapRelations(combined_rf, event_map)
        yield from EnumerateResolvedRFExecutions(cloned_threads, cloned_inits, cloned_rf)

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
        if is_indirect_location(r.location):
            continue
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

        # Build rf relations for this read-from selection.
        for process in processes:
            for e in process:
                if e in choice_map:
                    w = choice_map[e]
                    rf.append(Relation(w, e))

        results.append(rf)
    
    return results


def ApplyRFValues(threads, rf):
    rf_map = {rel.Last(): rel.First().value for rel in rf}

    for thread in threads:
        for event in thread:
            if event.type == Operation.READ and event in rf_map:
                event.value = rf_map[event]

def writes_by_location(processes):
    loc_writes = defaultdict(list)

    for process in processes:
        for e in process:
            if e.type == Operation.WRITE:
                loc_writes[e.location].append(e)

    return loc_writes

def canonicalize_location(location):
    return str(location).strip().strip("[]")

def extract_location_constraints(constraints, inits, processes):
    known_locations = set()

    for init in inits:
        known_locations.add(canonicalize_location(init.location))

    for process in processes:
        for event in process:
            if event.location is not None:
                if is_indirect_location(event.location):
                    continue
                known_locations.add(canonicalize_location(event.location))

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
        canonicalize_location(init.location): init.value
        for init in inits
    }

    for order in selection:
        if order:
            location_finals[canonicalize_location(order[0].location)] = order[-1].value

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
        separator = r" \/ "
        return f"exists ({separator.join(or_clauses)})"

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
                if is_indirect_location(event.location):
                    continue
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
        
    

# print("============================================")

# Allow specifying the litmus input file on the command line (positional, optional)
parser = argparse.ArgumentParser(description="Run a litmus test file")
parser.add_argument('input', nargs='?', default='input_test.litmus', help='Path to litmus test file')
args = parser.parse_args()
input_filename = args.input

parsed, initialization_events, constraints = parse_file(input_filename)
parsed = resolve_control_flow(parsed, constraints)
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
apply_read_values(converted, constraints)
resolve_indirect_locations(converted, constraints)
output_processed_litmus(initialization_events, converted, constraints)

rf = enumerate_rf_relations(converted, rf_candidates(converted, initialization_events)) # get final line from file?
# print("rf", rf)
fr = []

result = "Forbidden"
for rf_i in rf:
    cloned_threads, cloned_inits, event_map = CloneThreadsAndInits(converted, initialization_events)
    cloned_rf = RemapRelations(rf_i, event_map)

    for active_threads, active_inits, active_rf in EnumerateResolvedRFExecutions(
        cloned_threads,
        cloned_inits,
        cloned_rf,
    ):
        active_co_relations = enumerate_co_relations(active_threads, active_inits, constraints)

        for co_j in active_co_relations:
            fr = generate_fr_relations(active_rf, co_j)
            # Reject executions with a communication/po-loc cycle before building
            # the more expensive derived relations.
            if PerLocSC(active_threads, active_rf, fr, co_j):
                continue
            if Atomicity(active_threads, fr, co_j):
                continue

            test = Execution(active_threads, active_rf, fr, co_j)
            if(not NoThinAir(active_threads, active_rf) and not HappensBefore(test) and not PropagatesBefore(test) and not RCU(test)):
               result = "Allowed"
print(f"{input_filename}: {result}")
# test = Execution(converted, rf, fr, co) #[Relation(Event(1, "x", "Write", "Release", "C", 1, None), Event(2, "x", "Read", "Acquire", "C", 1, "r0")), Relation(Event(3, "y", "Write", "Relaxed", "C", 1, None), Event(0, "y", "Read", "Relaxed", "C", 1, "r1"))], [], [])
