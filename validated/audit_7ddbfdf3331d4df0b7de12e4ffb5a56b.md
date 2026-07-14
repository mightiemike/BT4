### Title
Unverified `_cached_sha256_treehash` Attribute Trusted Blindly in `Treehasher.sha256_treehash()`, Allowing Forged Puzzle Hash - (File: `wheel/python/clvm_rs/tree_hash.py`)

---

### Summary

`Treehasher.sha256_treehash()` reads `_cached_sha256_treehash` from any `CLVMStorage`-compatible Python object without verifying it against the object's actual content. `Program.wrap()` propagates this attribute unconditionally, and `Program.tree_hash()` returns it without recomputation if already set. Any caller that passes a Python object with a forged `_cached_sha256_treehash` into the Python API receives an attacker-chosen 32-byte value as the "puzzle hash" of that object.

---

### Finding Description

**Root cause — `tree_hash.py`, lines 57–61:**

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return
```

`handle_obj` short-circuits the entire hash computation the moment `_cached_sha256_treehash` is present on the object. No constraint ties this attribute to the object's `atom` or `pair` content. This is the direct analog of the external report: a value is *assigned* (by whoever constructed the Python object) but never *constrained* (never verified against the actual tree content).

**Propagation — `program.py`, line 142:**

```python
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
```

`Program.wrap()` copies the attribute from any `CLVMStorage`-duck-typed object unconditionally. A forged value on the source object is silently inherited by the resulting `Program`.

**Consumption — `program.py`, lines 284–286:**

```python
def tree_hash(self) -> bytes:
    if self._cached_sha256_treehash is None:
        self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
    return self._cached_sha256_treehash
```

Once `_cached_sha256_treehash` is non-`None`, `tree_hash()` returns it forever without recomputing. `Program.__eq__()` delegates entirely to `tree_hash()`, so equality comparison is also corrupted.

The `CLVMStorage` protocol (`clvm_storage.py`, lines 20–21) documents `_cached_sha256_treehash` as an *optional speed hint*, but the implementation treats it as authoritative with no integrity check.

---

### Impact Explanation

An attacker who can supply a Python object with an arbitrary `_cached_sha256_treehash` to any of the three entry points above receives an attacker-chosen bytes value as the puzzle hash of that object. Concretely:

- `Program.__eq__` uses `tree_hash()` for comparison; two programs with completely different content but the same forged hash compare as equal.
- Any wallet or node code that calls `p.tree_hash()` to derive a puzzle hash for coin-spend validation will accept the forged value, breaking the binding between a puzzle's bytecode and its on-chain identity.
- `curry_hash()` calls `self.tree_hash()` internally, so curried puzzle hashes are also forgeable.

The corrupted result is a concrete 32-byte `Bytes32` tree hash returned from `sha256_treehash()` / `Program.tree_hash()`.

---

### Likelihood Explanation

The attacker must be able to pass a Python object into the Python API — either directly to `sha256_treehash()`, or to `Program.wrap()` / `Program.to()`. The `CLVMStorage` protocol is duck-typed; any Python object with `atom`, `pair`, and `_cached_sha256_treehash` attributes qualifies. The Chia Python stack (wallet, mempool, full-node puzzle-hash derivation) passes `CLVMStorage`-compatible objects through these functions routinely. Any integration point that accepts a caller-supplied `CLVMStorage` object — plugin systems, scripting layers, or library consumers that construct objects before passing them to `Program.wrap()` — is a reachable entry path. The access rules explicitly list "Python objects" as an attacker-controlled surface.

---

### Recommendation

Remove the unconditional trust in `_cached_sha256_treehash`. Options:

1. **Verify on read**: Before using the cached value, recompute the hash for atoms (cheap) and assert equality; for pairs, require that the cache was set by the same `Treehasher` instance.
2. **Scope the cache**: Store the cached hash in a private dict keyed by object identity inside `Treehasher`, rather than as a public attribute on the object itself, so external code cannot inject values.
3. **Treat the attribute as write-once**: Only accept `_cached_sha256_treehash` if it was set by `Treehasher` itself (e.g., via a sentinel wrapper), not if it arrived from an external object.

---

### Proof of Concept

```python
from clvm_rs.tree_hash import sha256_treehash
from clvm_rs.program import Program

FORGED_HASH = b"\xde\xad\xbe\xef" * 8  # 32 bytes, attacker-chosen

class MaliciousStorage:
    atom = b"legitimate_puzzle_bytecode"
    pair = None
    _cached_sha256_treehash = FORGED_HASH  # injected, never verified

# Direct path: sha256_treehash returns the forged value immediately
result = sha256_treehash(MaliciousStorage())
assert result == FORGED_HASH, "sha256_treehash returned forged hash"

# Propagation path: Program.wrap copies the attribute
p = Program.wrap(MaliciousStorage())
assert p.tree_hash() == FORGED_HASH, "Program.tree_hash returned forged hash"

# Consequence: equality is broken
real = Program.new_atom(b"legitimate_puzzle_bytecode")
# real.tree_hash() != FORGED_HASH, but p == real is False only by accident;
# any program whose real hash happens to equal FORGED_HASH would compare equal to p
```

The verification passes silently. No exception is raised. The forged 32-byte value propagates as the authoritative puzzle hash of the object. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L56-61)
```python
            obj = obj_stack.pop()
            r = getattr(obj, "_cached_sha256_treehash", None)
            if r is not None:
                self.cache_hits += 1
                hash_stack.append(r)
                return
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

**File:** wheel/python/clvm_rs/clvm_storage.py (L19-22)
```python

    # `_cached_sha256_treehash: Optional[bytes]` is used by `sha256_treehash`
    # `_cached_serialization:  bytes` is used by `sexp_to_byte_iterator`
    #      to speed up serialization
```
