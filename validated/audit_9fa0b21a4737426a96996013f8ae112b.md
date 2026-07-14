### Title
Unverified User-Controlled `_cached_sha256_treehash` Accepted as Authoritative Puzzle Hash — (`File: wheel/python/clvm_rs/tree_hash.py`, `wheel/python/clvm_rs/program.py`)

---

### Summary

`Treehasher.sha256_treehash()` and `Program.wrap()` unconditionally trust the `_cached_sha256_treehash` attribute present on any `CLVMStorage`-compatible Python object, without ever verifying it against the object's actual content. An attacker who supplies a Python object implementing the `CLVMStorage` duck-typed protocol with a forged `_cached_sha256_treehash` causes the library to return an attacker-controlled value as the authoritative SHA-256 tree hash of that object. Because `Program.__eq__` and all puzzle-hash derivation paths are built on top of `tree_hash()`, this corrupts equality checks and puzzle-hash computation throughout the Python API layer.

---

### Finding Description

`Treehasher.sha256_treehash()` in `wheel/python/clvm_rs/tree_hash.py` reads `_cached_sha256_treehash` from every node it visits and, if the attribute is non-`None`, immediately uses it as the hash without any recomputation or cross-check:

```python
# tree_hash.py lines 57-61
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return          # ← forged value accepted unconditionally
``` [1](#0-0) 

`Program.wrap()` propagates the same attribute from any incoming `CLVMStorage` object directly into the new `Program` instance:

```python
# program.py line 142
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
``` [2](#0-1) 

`Program.tree_hash()` then short-circuits on the already-set field:

```python
# program.py lines 284-286
if self._cached_sha256_treehash is None:
    self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
return self._cached_sha256_treehash
``` [3](#0-2) 

The `CLVMStorage` protocol is explicitly duck-typed; any Python object with `.atom`, `.pair`, and `._cached_sha256_treehash` attributes is accepted by `Program.to()` → `Program.wrap()`. The `_cached_sha256_treehash` field is not part of the formal protocol definition but is silently consumed whenever present. [4](#0-3) 

---

### Impact Explanation

**Puzzle-hash forgery.** In Chia, coin addresses are SHA-256 tree hashes of puzzle programs. Wallet and node code derives puzzle hashes via `program.tree_hash()`. If a `CLVMStorage` object with `atom = b"<evil puzzle>"` and `_cached_sha256_treehash = <hash of innocent puzzle>` is passed to `Program.to()`, the returned `Program` reports the innocent puzzle's hash while serializing the evil puzzle bytes. Any downstream check that compares the reported hash against an expected address will pass, while the actual on-chain puzzle differs.

**Equality bypass.** `Program.__eq__` is implemented entirely through `tree_hash()` comparison:

```python
# program.py lines 182-188
def __eq__(self, other) -> bool:
    try:
        other_obj = self.to(other)
    except ValueError:
        return False
    return self.tree_hash() == other_obj.tree_hash()
``` [5](#0-4) 

Two programs with entirely different content but the same forged `_cached_sha256_treehash` compare as equal. Two programs with identical content but different forged hashes compare as unequal. Both directions are exploitable.

**Subtree poisoning.** Because `sha256_treehash` trusts the cached value on every visited node (not just the root), a single malicious leaf embedded anywhere in a larger tree corrupts the hash of every ancestor pair, including the root. [6](#0-5) 

---

### Likelihood Explanation

The `CLVMStorage` protocol is a public, documented duck-typed interface. `Program.to()` is the primary entry point for converting arbitrary Python values into CLVM programs and is called throughout wallet and node code. Any code path that accepts a `CLVMStorage`-compatible object from an external source (plugin, deserialized Python object, cross-process IPC, or a third-party library implementing the protocol) and passes it to `Program.to()` or directly to `sha256_treehash()` is reachable. The `SimpleStorage` class in the test suite demonstrates that implementing the protocol with arbitrary attribute values is trivial. [7](#0-6) 

---

### Recommendation

Remove the unconditional trust in `_cached_sha256_treehash` when the object originates from outside the library's own code paths. Concretely:

1. In `Treehasher.sha256_treehash()`, only use `_cached_sha256_treehash` as a cache hit if the object is an instance of a trusted internal type (e.g., `Program` or `LazyNode`) whose hash was set by the library itself, not by arbitrary external objects.
2. In `Program.wrap()`, do not copy `_cached_sha256_treehash` from an incoming `CLVMStorage` object unless it is already a `Program` instance (the existing `isinstance(v, Program)` fast-path already handles this correctly; the issue is the `getattr` fallback for all other types).
3. Alternatively, compute and verify the hash from the actual atom/pair content before accepting a cached value from an external object.

---

### Proof of Concept

```python
from clvm_rs import Program
from clvm_rs.clvm_storage import CLVMStorage

# Honest program: atom b"innocent"
honest = Program.to(b"innocent")
honest_hash = honest.tree_hash()

# Malicious CLVMStorage: content is b"evil" but hash claims to be honest's hash
class MaliciousStorage(CLVMStorage):
    atom = b"evil"
    pair = None
    _cached_sha256_treehash = honest_hash   # forged

malicious = Program.to(MaliciousStorage())

# tree_hash() returns the forged value, not sha256tree(b"evil")
assert malicious.tree_hash() == honest_hash          # forged hash accepted
assert malicious.tree_hash() != Program.to(b"evil").tree_hash()  # real hash differs

# __eq__ is corrupted: different content, same reported hash
assert malicious == honest   # passes — equality bypass

# bytes() reveals the actual evil content
assert bytes(malicious) != bytes(honest)  # content differs
```

The `_cached_sha256_treehash` is read at `tree_hash.py:57` and propagated at `program.py:142` without any verification against the actual atom bytes or pair structure, making the forged hash the authoritative identity of the object throughout the Python API. [8](#0-7) [9](#0-8)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L50-100)
```python
    def sha256_treehash(self, clvm_storage: CLVMStorage) -> bytes:
        def handle_obj(
            obj_stack: List[CLVMStorage],
            hash_stack: List[bytes],
            op_stack: List[OP_STACK_F],
        ) -> None:
            obj = obj_stack.pop()
            r = getattr(obj, "_cached_sha256_treehash", None)
            if r is not None:
                self.cache_hits += 1
                hash_stack.append(r)
                return
            elif obj.atom is not None:
                r = shatree_atom(obj.atom)
                hash_stack.append(r)
                try:
                    setattr(obj, "_cached_sha256_treehash", r)
                except AttributeError:
                    pass
            else:
                pair = cast(Tuple[CLVMStorage, CLVMStorage], obj.pair)
                p0, p1 = pair
                obj_stack.append(obj)
                obj_stack.append(p0)
                obj_stack.append(p1)
                op_stack.append(handle_pair)
                op_stack.append(handle_obj)
                op_stack.append(handle_obj)

        def handle_pair(
            obj_stack: List[CLVMStorage],
            hash_stack: List[bytes],
            op_stack: List[OP_STACK_F],
        ) -> None:
            p0 = hash_stack.pop()
            p1 = hash_stack.pop()
            r = shatree_pair(p0, p1)
            hash_stack.append(r)
            obj = obj_stack.pop()
            try:
                setattr(obj, "_cached_sha256_treehash", r)
            except AttributeError:
                pass

        obj_stack: List[CLVMStorage] = [clvm_storage]
        op_stack: List[OP_STACK_F] = [handle_obj]
        hash_stack: List[bytes] = []
        while len(op_stack) > 0:
            op: OP_STACK_F = op_stack.pop()
            op(obj_stack, hash_stack, op_stack)
        return hash_stack[0]
```

**File:** wheel/python/clvm_rs/program.py (L128-143)
```python
    @classmethod
    def to(cls, v: CastableType) -> Program:
        return cls.wrap(to_clvm_object(v, cls.new_atom, cls.new_pair))

    @classmethod
    def wrap(cls, v: CLVMStorage) -> Program:
        if isinstance(v, Program):
            return v
        o = cls()
        o.atom = v.atom
        o._pair = None
        o._unwrapped = v
        o._unwrapped_pair = v.pair
        o._cached_serialization = getattr(v, "_cached_serialization", None)
        o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
        return o
```

**File:** wheel/python/clvm_rs/program.py (L182-188)
```python
    def __eq__(self, other) -> bool:
        try:
            other_obj = self.to(other)
        except ValueError:
            # cast failure
            return False
        return self.tree_hash() == other_obj.tree_hash()
```

**File:** wheel/python/clvm_rs/program.py (L284-286)
```python
        if self._cached_sha256_treehash is None:
            self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
        return self._cached_sha256_treehash
```

**File:** wheel/python/tests/test_program.py (L175-188)
```python
class SimpleStorage(CLVMStorage):
    """
    A simple implementation of `CLVMStorage`.
    """

    atom: Optional[bytes]

    def __init__(self, atom, pair):
        self.atom = atom
        self._pair = pair

    @property
    def pair(self) -> Optional[Tuple["CLVMStorage", "CLVMStorage"]]:
        return self._pair
```
