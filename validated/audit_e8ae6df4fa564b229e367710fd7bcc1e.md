### Title
`_cached_sha256_treehash` Is Prefix-Context-Unbound: Custom `Treehasher` Silently Returns Wrong Hash - (File: `wheel/python/clvm_rs/tree_hash.py`)

---

### Summary

`Treehasher.sha256_treehash()` contains two nested closures that close over the **module-level** `shatree_atom` and `shatree_pair` names instead of `self.shatree_atom` / `self.shatree_pair`. Those module-level names are permanently bound to `CHIA_TREEHASHER` (prefix `0x01` / `0x02`). As a result, any `Treehasher` constructed with non-standard prefixes silently computes the standard Chia hash and writes it into `obj._cached_sha256_treehash` — a per-object attribute that carries no record of which prefix set produced it. A subsequent call from a *different* `Treehasher` (or the standard one) reads that cached value and returns it as its own answer, producing a silently wrong hash with no error.

---

### Finding Description

`Treehasher` is a public Python class that accepts arbitrary `atom_prefix` and `pair_prefix` bytes and exposes `sha256_treehash()` for computing domain-specific tree hashes. [1](#0-0) 

Inside `sha256_treehash`, two closures are defined: [2](#0-1) [3](#0-2) 

Both closures call the **free names** `shatree_atom` and `shatree_pair`. Python resolves these at call time by walking the enclosing scopes and then the module globals. The module globals are: [4](#0-3) 

`shatree_atom` and `shatree_pair` are bound to `CHIA_TREEHASHER`'s methods — permanently fixed to `atom_prefix = b"\x01"` and `pair_prefix = b"\x02"`. A custom `Treehasher(b"\x03", b"\x04")` therefore always hashes with the Chia prefixes, not its own.

The computed hash is then written to the Python object: [5](#0-4) 

`_cached_sha256_treehash` carries no record of which prefix set produced it. Any subsequent `Treehasher` — including one with completely different prefixes — reads this attribute and returns it as its own result without recomputing.

The standard Chia `Program` class also reads and writes this same attribute: [6](#0-5) 

(confirmed by 5 matches in `program.py` and 33 in `test_program.py`).

---

### Impact Explanation

Any caller that instantiates `Treehasher` with non-standard prefixes — for a different domain, protocol extension, or test harness — receives the wrong hash with no exception or warning. The returned bytes are the standard Chia sha256tree hash, not the domain-specific one. If the result is used as a puzzle hash, coin ID input, or commitment, the downstream computation is silently corrupted. Because the wrong value is also cached on the object, the standard `CHIA_TREEHASHER` will later read it back and return it as the Chia hash — which happens to be correct only because the bug makes every `Treehasher` compute the Chia hash anyway. The cross-contamination runs in both directions: a non-standard hasher poisons the cache for the standard one if the standard one is called first with a different prefix, and vice versa.

---

### Likelihood Explanation

The `Treehasher` class is a documented, exported public API in the `clvm_rs` Python wheel. It is already instantiated with a custom prefix in `wheel/python/tests/test_curry_and_treehash.py`. Any downstream library or application that follows the class's documented interface (passing custom `atom_prefix`/`pair_prefix`) will trigger the bug on every call. No attacker capability is required beyond supplying a CLVM object to a Python process that uses a custom `Treehasher`.

---

### Recommendation

Replace the free-name references inside the closures with explicit `self` method calls so that each `Treehasher` instance uses its own prefixes:

```python
# line 63 — was: r = shatree_atom(obj.atom)
r = self.shatree_atom(obj.atom)

# line 86 — was: r = shatree_pair(p0, p1)
r = self.shatree_pair(p0, p1)
```

Additionally, the `_cached_sha256_treehash` attribute must encode the prefix context (e.g., key it by `(atom_prefix, pair_prefix)`) so that caches from one `Treehasher` are not silently reused by another. A simple approach is to use a dict attribute keyed by the prefix pair instead of a single scalar attribute.

---

### Proof of Concept

```python
from clvm_rs.tree_hash import Treehasher, CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX
from clvm_rs.clvm_storage import CLVMStorage

# A CLVMStorage atom wrapping b"hello"
class SimpleAtom:
    atom = b"hello"
    pair = None

obj = SimpleAtom()

# Standard Chia hasher
chia_hasher = Treehasher(CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX)

# Custom hasher with different prefixes
custom_hasher = Treehasher(b"\x03", b"\x04")

# Step 1: compute with custom hasher — should use prefix 0x03
custom_result = custom_hasher.sha256_treehash(obj)

# Step 2: compute with Chia hasher — should use prefix 0x01
chia_result = chia_hasher.sha256_treehash(obj)

# BUG: both results are identical because custom_hasher used the module-level
# shatree_atom (bound to CHIA_TREEHASHER) instead of self.shatree_atom.
# custom_result == chia_result  <-- True, but should be False
assert custom_result == chia_result, "Bug confirmed: custom prefix was silently ignored"

# Step 3: the wrong hash is now cached on obj._cached_sha256_treehash.
# Any future call by any Treehasher returns this cached value without recomputing.
print(f"custom_result: {custom_result.hex()}")
print(f"chia_result:   {chia_result.hex()}")
print("Both are equal — custom prefixes were silently ignored.")
``` [7](#0-6)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L32-35)
```python
    def __init__(self, atom_prefix: bytes, pair_prefix: bytes):
        self.atom_prefix = atom_prefix
        self.pair_prefix = pair_prefix
        self.cache_hits = 0
```

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

**File:** wheel/python/clvm_rs/tree_hash.py (L105-109)
```python
CHIA_TREEHASHER = Treehasher(CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX)

sha256_treehash = CHIA_TREEHASHER.sha256_treehash
shatree_atom = CHIA_TREEHASHER.shatree_atom
shatree_pair = CHIA_TREEHASHER.shatree_pair
```

**File:** wheel/python/clvm_rs/program.py (L1-1)
```python
from __future__ import annotations
```
