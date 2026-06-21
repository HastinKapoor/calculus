from enum import Enum
import json

class Operation(Enum):
    READ = 1
    WRITE = 2
    FENCE = 3
    RMW = 4
    RMW_R = 5
    RMW_W = 6

class MemoryOrder(Enum):
    INITIAL = 0
    RELAXED = 1
    ACQUIRE = 2
    RELEASE = 3
    ACQ_REL = 4
    SEQ_CST = 5
    WMB = 6
    RMB = 7
    SYNC_RCU = 8
    RCU_LOCK = 9
    RCU_UNLOCK = 10
    LOCK_READ = 11
    LOCK_WRITE = 12
    UNLOCK = 13
    AFTER_SPINLOCK = 14
    AFTER_UNLOCK_LOCK = 15
    
class Language(Enum):
    C = 1
    LINUX = 2

class Identifier:
    def __init__(self, thread_id: int, event_id: int):
        self.thread_id = thread_id
        self.event_id = event_id
    
    def __eq__(self, other):
        if not isinstance(other, Identifier):
            return False
        return (self.thread_id == other.thread_id) and (self.event_id == other.event_id)

    def __hash__(self):
        return hash((self.thread_id, self.event_id))
    
    def __repr__(self):
        return f"{self.thread_id}_{self.event_id}"
    
    def __str__(self):
        return f"{self.thread_id}_{self.event_id}"
    
    def getThreadId(self):
        return self.thread_id
    
class IterativeIdentifier:
    def __init__(self, thread_id: int):
        self.thread_id = thread_id
        self.current_op = 0
    
    def next_id(self) -> Identifier:
        new_id = Identifier(self.thread_id, self.current_op)
        self.current_op += 1
        return new_id
    
class Register:
    def __init__(self, thread_id: int, id: str):
        self.thread_id = thread_id
        self.id = id
    
    def __str__(self):
        return f"{self.thread_id}:{self.id}"
    
    def __repr__(self):
        return f"{self.id}"
    
    def to_dict(self):
        return self.__repr__()
        
class GlobalRegisters:
    def __init__(self, num_threads: int):
        self.perThreadRegisters = [ list() for _ in range(num_threads) ]
        self.allRegisters = dict()
        
    def newRegister(self, thread_id: int, register: str) -> Register:
        if register in self.allRegisters:
            print(f"ERROR: Register {register} is already in use in Thread {self.allRegisters[register]}")
        else:
            new_register = Register(thread_id=thread_id, id=register)
            self.perThreadRegisters[thread_id].append(new_register)
            self.allRegisters[new_register] = thread_id
            return new_register
        
        exit(0)
            
    # def newRegisterForThread(self, thread_id: int) -> Register:
    #     register_id = len(self.perThreadRegisters[thread_id])
    #     new_register = Register(thread_id=thread_id, id=str(register_id))
    #     self.perThreadRegisters[thread_id].append(new_register)
    #     self.allRegisters[new_register] = thread_id
    #     return new_register
    
    # def __str__(self):
    #     return json.dumps(self.allRegisters, indent=2, default=str)
        
    
            
