import Std

namespace Calculus

inductive Operation where
  | read
  | write
  | fence
  | rmw
  | rmwR
  | rmwW
  deriving Repr, DecidableEq, BEq, Hashable, Inhabited

inductive MemoryOrder where
  | initial
  | relaxed
  | acquire
  | release
  | acqRel
  | seqCst
  | wmb
  | rmb
  deriving Repr, DecidableEq, BEq, Hashable, Inhabited

inductive Language where
  | c
  | linux
  deriving Repr, DecidableEq, BEq, Hashable, Inhabited

structure Identifier where
  threadId : Nat
  eventId : Nat
  deriving Repr, DecidableEq, BEq, Hashable, Inhabited

structure Register where
  threadId : Nat
  name : String
  deriving Repr, DecidableEq, BEq, Hashable, Inhabited

structure Event where
  identifier : Identifier
  location : Option String
  op : Operation
  strength : MemoryOrder
  language : Language
  value : Option Int
  register : Option Register
  deriving Repr, DecidableEq, BEq, Hashable, Inhabited

mutual
  inductive RelAtom where
    | event : Event → RelAtom
    | relation : Relation → RelAtom
    deriving Repr, Inhabited

  structure Relation where
    parts : List RelAtom
    deriving Repr, Inhabited
end

deriving instance BEq for RelAtom
deriving instance Hashable for RelAtom
deriving instance BEq for Relation
deriving instance Hashable for Relation

abbrev Thread := List Event

def Relation.first? (r : Relation) : Option RelAtom :=
  r.parts.head?

def Relation.last? (r : Relation) : Option RelAtom :=
  r.parts.getLast?

mutual
  partial def RelAtom.initialEvent? : RelAtom → Option Event
    | .event e => some e
    | .relation r => r.initialEvent?

  partial def Relation.initialEvent? (r : Relation) : Option Event :=
    r.first?.bind RelAtom.initialEvent?
end

mutual
  partial def RelAtom.terminalEvent? : RelAtom → Option Event
    | .event e => some e
    | .relation r => r.terminalEvent?

  partial def Relation.terminalEvent? (r : Relation) : Option Event :=
    r.last?.bind RelAtom.terminalEvent?
end

mutual
  partial def RelAtom.firstChain : RelAtom → List RelAtom
    | .event _ => []
    | .relation r => r.firstChain

  partial def Relation.firstChain (r : Relation) : List RelAtom :=
    match r.first? with
    | none => []
    | some x => x :: x.firstChain
end

mutual
  partial def RelAtom.lastChain : RelAtom → List RelAtom
    | .event _ => []
    | .relation r => r.lastChain

  partial def Relation.lastChain (r : Relation) : List RelAtom :=
    match r.last? with
    | none => []
    | some x => x :: x.lastChain
end

def Relation.composes (a b : Relation) : Bool :=
  let left := a.lastChain
  let right := b.firstChain
  left.any fun x => right.any fun y => x == y

mutual
  partial def RelAtom.containsEvent : RelAtom → Bool
    | .event _ => true
    | .relation r => r.containsEvent

  partial def Relation.containsEvent (r : Relation) : Bool :=
    r.parts.any RelAtom.containsEvent
end

def inEmpty (r : Relation) : Bool :=
  not r.containsEvent

def inID (r : Relation) : Bool :=
  r.composes r

def inInt (threads : List Thread) (r : Relation) : Bool :=
  match r.initialEvent?, r.terminalEvent? with
  | some i, some t => threads.any fun thread => thread.contains i && thread.contains t
  | _, _ => false

def mkRel (xs : List RelAtom) : Relation := { parts := xs }

structure Execution where
  threads : List Thread
  rf : List Relation
  fr : List Relation
  co : List Relation
  ppo : List Relation
  st : List Relation
  prop : List Relation
  deriving Repr

end Calculus
