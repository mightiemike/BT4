### Title
`Treehasher.sha256_treehash` ignores instance `atom_prefix`/`pair_prefix` and poisons `_cached_sha256_treehash` with CHIA-only hashes, enabling cross-instance hash mismatch — (`wheel/python/clvm_rs/tree_hash.py`)

---

### Summary

`Treehasher` is designed to support configurable `atom_prefix` and `pair_prefix` values. Its instance methods `shatree_atom` and `shatree_pair` correctly use `self.atom_prefix` and `self.pair_prefix`. However, the inner closures `handle_obj` and `handle_pair` inside `sha256_treehash` call the **module-level** `shatree_atom` (line 63) and `shatree_pair` (line 86), which are permanently bound to `CHIA_TREEHASHER`'s methods (CHIA prefixes `0x01`/`0x02`). The configured instance prefixes are silently ignored during the actual hash computation. Additionally, the CHIA-prefix hash is written into `_cached_sha256_treehash` on the underlying Python object, making that stale value available to any subsequent `Treehasher` call regardless of its prefix configuration.

---

### Finding Description

In `wheel/python/clvm_rs/tree_hash.py`, the module-level names are assigned after `CHIA_TREEHASHER` is constructed:

```python
# wheel/python/clvm_rs/tree_hash.py lines 107-109
sha256_treehash = CHIA_TREEHASHER.sha256_treehash
shatree_atom    = CHIA_TREEHASHER.shatree_atom   # bound to CHIA prefix 0x01
shatree_pair    = CHIA_TREEHASHER.shatree_pair   # bound to CHIA prefix 0x02
```

Inside `Treehasher.sha256_treehash`, the two closures reference these module-level names, not `self`:

```python
# line 63 — inside handle_obj
r = shatree_atom(obj.atom)          # always CHIA_TREEHASHER.shatree_atom

# line 86 — inside handle_pair
r = shatree_pair(p0, p1)            # always CHIA_TREEHASHER.shatree_pair
```

The result `r` is then written back to the object:

```python
setattr(obj, "_cached_sha256_treehash", r)   # lines 66, 90
```

A subsequent call to **any** `Treehasher` instance on the same object hits the early-return path at line 57–61 and returns the cached CHIA hash without recomputation:

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    hash_stack.append(r)   # stale CHIA hash returned to custom Treehasher
    return
```

This is structurally identical to the reported vulnerability: a parameter that governs the hash domain (`atom_prefix`/`pair_prefix`) is omitted from the actual hash computation, so the same cached digest is reused across different hash-domain contexts.

---

### Impact Explanation

Any caller that instantiates a `Treehasher` with non-CHIA prefixes (e.g., for a different hash domain, a test harness, or a future protocol extension) will silently receive CHIA-domain hashes instead of the expected custom-domain hashes. Because the wrong hash is also written into `_cached_sha256_treehash` on the shared Python object, the poisoned value propagates to every future `sha256_treehash` call on that object — including calls through `Program.tree_hash()` (line 285 of `program.py`), which is the primary puzzle-hash API used by wallets and coin-spend validation. If a `CLVMStorage` object is shared between a custom-prefix `Treehasher` and `CHIA_TREEHASHER`, the first caller to hash it determines the cached value for all subsequent callers, regardless of their prefix configuration.

---

### Likelihood Explanation

The standard Chia protocol only instantiates `CHIA_TREEHASHER`, so the bug is dormant in the common path. However, the `Treehasher` class is a public, documented API with configurable prefixes, and `Program.wrap` explicitly propagates `_cached_sha256_treehash` from wrapped objects (line 142 of `program.py`). Any downstream library or tool that creates a custom `Treehasher` — or that passes a `CLVMStorage` object whose `_cached_sha256_treehash` was set by a different prefix context — will silently receive wrong puzzle hashes. The `CLVMTree` class already pre-populates `_cached_sha256_treehash` from an externally supplied `tree_hashes` list (line 82 of `clvm_tree.py`), widening the surface for stale-cache injection.

---

### Recommendation

Replace the module-level name references inside the closures with `self`-qualified calls:

```python
# handle_obj, line 63
- r = shatree_atom(obj.atom)
+ r = self.shatree_atom(obj.atom)

# handle_pair, line 86
- r = shatree_pair(p0, p1)
+ r = self.shatree_pair(p0, p1)
```

Additionally, the `_cached_sha256_treehash` attribute should encode the prefix context (e.g., keyed by `(atom_prefix, pair_prefix)`) so that a hash computed by one `Treehasher` is not silently reused by another with different prefixes.

---

### Proof of Concept

```python
from clvm_rs.tree_hash import Treehasher, CHIA_TREEHASHER
from clvm_rs.clvm_storage import CLVMStorage

class SimpleAtom(CLVMStorage):
    def __init__(self, data: bytes):
        self.atom = data
    @property
    def pair(self): return None

atom = SimpleAtom(b"hello")

# Step 1: hash with CHIA_TREEHASHER — sets _cached_sha256_treehash with prefix 0x01
chia_hash = CHIA_TREEHASHER.sha256_treehash(atom)
print("CHIA hash:", chia_hash.hex())
# _cached_sha256_treehash is now set on `atom`

# Step 2: create a custom Treehasher with different prefixes
custom = Treehasher(atom_prefix=b'\x03', pair_prefix=b'\x04')

# Step 3: hash the same object — should use prefix 0x03, but returns cached CHIA hash
custom_hash = custom.sha256_treehash(atom)
print("Custom hash:", custom_hash.hex())

# Both are identical — custom Treehasher silently returned the CHIA hash
assert chia_hash == custom_hash, "Cross-instance hash reuse confirmed"

# Verify what the correct custom hash should be
import hashlib
expected = hashlib.sha256(b'\x03' + b'hello').digest()
print("Expected custom hash:", expected.hex())
assert custom_hash != expected  # custom_hash is wrong
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L32-48)
```python
    def __init__(self, atom_prefix: bytes, pair_prefix: bytes):
        self.atom_prefix = atom_prefix
        self.pair_prefix = pair_prefix
        self.cache_hits = 0

    def shatree_atom(self, atom: bytes) -> bytes:
        s = sha256()
        s.update(self.atom_prefix)
        s.update(atom)
        return s.digest()

    def shatree_pair(self, left_hash: bytes, right_hash: bytes) -> bytes:
        s = sha256()
        s.update(self.pair_prefix)
        s.update(left_hash)
        s.update(right_hash)
        return s.digest()
```

**File:** wheel/python/clvm_rs/tree_hash.py (L56-68)
```python
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
```

**File:** wheel/python/clvm_rs/tree_hash.py (L79-92)
```python
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
```

**File:** wheel/python/clvm_rs/tree_hash.py (L103-109)
```python
CHIA_TREE_HASH_ATOM_PREFIX = bytes.fromhex("01")
CHIA_TREE_HASH_PAIR_PREFIX = bytes.fromhex("02")
CHIA_TREEHASHER = Treehasher(CHIA_TREE_HASH_ATOM_PREFIX, CHIA_TREE_HASH_PAIR_PREFIX)

sha256_treehash = CHIA_TREEHASHER.sha256_treehash
shatree_atom = CHIA_TREEHASHER.shatree_atom
shatree_pair = CHIA_TREEHASHER.shatree_pair
```

**File:** wheel/python/clvm_rs/program.py (L141-143)
```python
        o._cached_serialization = getattr(v, "_cached_serialization", None)
        o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
        return o
```

**File:** wheel/python/clvm_rs/program.py (L281-286)
```python
    def tree_hash(self) -> bytes:
        # we operate on the unwrapped version to prevent the re-wrapping that
        # happens on each invocation of `Program.pair` whenever possible
        if self._cached_sha256_treehash is None:
            self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
        return self._cached_sha256_treehash
```

**File:** wheel/python/clvm_rs/clvm_tree.py (L81-83)
```python
        if self.tree_hashes:
            self._cached_sha256_treehash = self.tree_hashes[index]
        start, end, atom_offset = self.int_tuples[self.index]
```
