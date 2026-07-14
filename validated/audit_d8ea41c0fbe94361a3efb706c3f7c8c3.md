### Title
Stale `_cached_sha256_treehash` Blindly Inherited by `Program.wrap()` Without Verification — (File: `wheel/python/clvm_rs/program.py`)

### Summary

`Program.wrap()` unconditionally copies `_cached_sha256_treehash` from any caller-supplied `CLVMStorage` object without verifying it matches the object's actual content. Because `Program.tree_hash()` short-circuits on a non-`None` cached value, a pre-set wrong hash is returned verbatim. `Program.__eq__()` delegates entirely to `tree_hash()`, so two structurally different programs can compare equal — or two identical programs can compare unequal — depending on what hash the caller planted. This is the direct analog of the Dharma key-reuse bug: stale cached state is trusted without any invalidation mechanism, and the API provides no way for callers to know the invariant has been broken.

### Finding Description

`Program.wrap()` is the single entry point through which every external `CLVMStorage` object enters the `Program` type:

```python
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
    o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)  # ← blind copy
    return o
``` [1](#0-0) 

`Program.tree_hash()` then returns the cached value without recomputing:

```python
def tree_hash(self) -> bytes:
    if self._cached_sha256_treehash is None:
        self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
    return self._cached_sha256_treehash
``` [2](#0-1) 

`Program.__eq__()` uses only `tree_hash()` for identity:

```python
def __eq__(self, other) -> bool:
    try:
        other_obj = self.to(other)
    except ValueError:
        return False
    return self.tree_hash() == other_obj.tree_hash()
``` [3](#0-2) 

The `CLVMStorage` protocol explicitly documents `_cached_sha256_treehash` as an optional field that any implementation may set: [4](#0-3) 

The `Treehasher.sha256_treehash()` function also writes computed hashes back to sub-objects via `setattr`, meaning any mutable `CLVMStorage` object that is shared across two different tree contexts can have its cached hash overwritten by a traversal of a different tree that happens to share the same Python object identity: [5](#0-4) 

There is no invalidation path. Once `_cached_sha256_treehash` is set on a `CLVMStorage` object — whether correctly or not — every subsequent `Program.wrap()` call on that object will inherit and permanently trust it.

### Impact Explanation

`Program.tree_hash()` is the canonical puzzle-hash computation used throughout Chia's wallet and mempool stack. Puzzle hashes identify coins: a coin is locked to a specific puzzle hash, and a spend is valid only if the presented puzzle hashes to that value. If `tree_hash()` returns a wrong value:

- `Program.__eq__()` produces wrong equality results, causing a program containing atom `A` to compare equal to a program containing atom `B`.
- Downstream callers that use `tree_hash()` to derive coin IDs or validate spend bundles will operate on the wrong hash, potentially accepting an invalid spend or rejecting a valid one.
- The `_cached_serialization` field is subject to the same blind-copy at line 141, meaning `bytes(program)` can also return stale bytes, causing `run_with_cost()` to execute the wrong serialized program. [6](#0-5) 

### Likelihood Explanation

The `CLVMStorage` protocol is explicitly designed for third-party implementation. Any library that:

1. Implements `CLVMStorage` with a mutable `atom` field and sets `_cached_sha256_treehash` at construction time, then mutates `atom` later, or
2. Intentionally pre-sets `_cached_sha256_treehash` to a chosen value before passing the object to `Program.wrap()` or `Program.to()`

will trigger this path. The `Program.to()` call chain accepts arbitrary Python objects and routes them through `wrap()`. No special privilege is required beyond the ability to pass a Python object to the public API. The `CLVMStorage` documentation actively invites third-party implementations to set this field for performance.

### Recommendation

**Short term:** In `Program.wrap()`, do not inherit `_cached_sha256_treehash` from the wrapped object. Remove line 142 (`o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)`). The first call to `tree_hash()` will compute and cache the correct value from the actual content.

**Long term:** Make `Program` objects structurally immutable (freeze `atom` and `pair` after construction) and document that `_cached_sha256_treehash` must only be set by the `sha256_treehash` computation itself, never by external callers. Add a test that wraps a `CLVMStorage` object with a pre-set wrong hash and asserts that `Program.tree_hash()` returns the correct value.

### Proof of Concept

```python
from clvm_rs.clvm_storage import CLVMStorage
from clvm_rs.program import Program

class PoisonedStorage(CLVMStorage):
    def __init__(self, real_atom: bytes, fake_hash: bytes):
        self.atom = real_atom
        self._pair = None
        # Plant a hash that belongs to a completely different program
        self._cached_sha256_treehash = fake_hash

    @property
    def pair(self):
        return self._pair

# Compute the correct hash for b"bar"
bar_hash = Program.to(b"bar").tree_hash()

# Wrap b"foo" but plant the hash of b"bar"
poisoned = PoisonedStorage(b"foo", bar_hash)
p = Program.wrap(poisoned)

# Program.wrap() blindly inherited the wrong hash
assert p.tree_hash() == bar_hash          # True — wrong hash returned
assert p == Program.to(b"bar")            # True — equality broken
assert p != Program.to(b"foo")            # True — p does not equal its own content

# The serialized bytes are correct (b"foo"), but tree_hash disagrees
assert bytes(p) == bytes(Program.to(b"foo"))  # True
# Invariant violated: bytes equal but tree_hash unequal
assert p.tree_hash() != Program.to(b"foo").tree_hash()  # True
```

The broken invariant — `bytes(p1) == bytes(p2)` must imply `p1.tree_hash() == p2.tree_hash()` — is violated without any error or warning, silently corrupting all downstream puzzle-hash and equality logic.

### Citations

**File:** wheel/python/clvm_rs/program.py (L87-92)
```python
    def __bytes__(self) -> bytes:
        if self._cached_serialization is None:
            self._cached_serialization = sexp_to_bytes(self)
        if not isinstance(self._cached_serialization, bytes):
            self._cached_serialization = bytes(self._cached_serialization)
        return self._cached_serialization
```

**File:** wheel/python/clvm_rs/program.py (L132-143)
```python
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

**File:** wheel/python/clvm_rs/program.py (L281-286)
```python
    def tree_hash(self) -> bytes:
        # we operate on the unwrapped version to prevent the re-wrapping that
        # happens on each invocation of `Program.pair` whenever possible
        if self._cached_sha256_treehash is None:
            self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
        return self._cached_sha256_treehash
```

**File:** wheel/python/clvm_rs/clvm_storage.py (L18-22)
```python
    # optional fields used to speed implementations:

    # `_cached_sha256_treehash: Optional[bytes]` is used by `sha256_treehash`
    # `_cached_serialization:  bytes` is used by `sexp_to_byte_iterator`
    #      to speed up serialization
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
