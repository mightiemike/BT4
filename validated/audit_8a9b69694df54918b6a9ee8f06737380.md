### Title
Unconditionally Trusted `_cached_sha256_treehash` on Arbitrary Python Objects Causes Puzzle Hash Spoofing — (`File: wheel/python/clvm_rs/tree_hash.py`, `wheel/python/clvm_rs/program.py`)

---

### Summary

`Treehasher.sha256_treehash()` and `Program.wrap()` unconditionally trust the `_cached_sha256_treehash` attribute on any `CLVMStorage` object passed to them, without verifying that the cached value matches the object's actual content. An attacker-controlled Python object implementing `CLVMStorage` with a forged `_cached_sha256_treehash` causes `Program.tree_hash()` to return a wrong puzzle hash, corrupting all downstream operations that depend on it — including `Program.__eq__()`, coin identification, and puzzle hash verification.

---

### Finding Description

In `wheel/python/clvm_rs/tree_hash.py`, `Treehasher.sha256_treehash()` reads `_cached_sha256_treehash` from any `CLVMStorage` object and, if non-`None`, immediately returns it as the authoritative hash without any recomputation or validation:

```python
r = getattr(obj, "_cached_sha256_treehash", None)
if r is not None:
    self.cache_hits += 1
    hash_stack.append(r)
    return   # ← cached value returned unconditionally
``` [1](#0-0) 

`Program.wrap()` propagates this cached value from any arbitrary `CLVMStorage` object into the new `Program` wrapper without verification:

```python
o._cached_sha256_treehash = getattr(v, "_cached_sha256_treehash", None)
``` [2](#0-1) 

`Program.tree_hash()` then short-circuits on the already-set cached value:

```python
if self._cached_sha256_treehash is None:
    self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
return self._cached_sha256_treehash
``` [3](#0-2) 

The result is that the cached hash is treated as immutable truth — exactly analogous to EIP712's immutable `DOMAIN_SEPARATOR` — even though the underlying content may differ from what the hash represents.

---

### Impact Explanation

`Program.__eq__()` delegates entirely to `tree_hash()`:

```python
return self.tree_hash() == other_obj.tree_hash()
``` [4](#0-3) 

A forged `_cached_sha256_treehash` causes:

1. **Puzzle hash spoofing**: A coin with puzzle content X reports puzzle hash H(Y), making it appear to be a different coin to any wallet or node that calls `Program.tree_hash()`.
2. **Equality confusion**: Two structurally different programs compare as equal (or the same program compares as unequal) because `__eq__` uses the forged hash.
3. **Curry hash corruption**: `Program.curry_hash()` calls `self.tree_hash()` to seed the curry computation; a forged base hash propagates into all derived curry hashes. [5](#0-4) 

---

### Likelihood Explanation

The `CLVMStorage` protocol is a plain Python duck-typed interface. Any Python object with `.atom`, `.pair`, and optionally `._cached_sha256_treehash` attributes satisfies it. The scope explicitly includes attacker-controlled Python objects as an entry path. Concrete realistic scenarios:

- A malicious or compromised third-party library provides `CLVMStorage` objects with pre-set forged hashes and passes them to wallet code that calls `Program.to()` or `sha256_treehash()`.
- A plugin or serialization adapter constructs `CLVMStorage` objects from untrusted external data and sets `_cached_sha256_treehash` from that data before passing to `Program.wrap()`.
- A `Program` object's `atom` field is mutated after the hash is cached (the class documents itself as immutable but Python does not enforce this), causing the cached hash to permanently diverge from the actual content. [6](#0-5) 

---

### Recommendation

1. **Do not trust `_cached_sha256_treehash` from externally-supplied objects.** In `Program.wrap()`, drop the line that copies `_cached_sha256_treehash` from the wrapped object. Only set it after computing it from the actual content.
2. **In `Treehasher.sha256_treehash()`**, only use the cached value if the object is a known-trusted type (e.g., `Program` itself, whose cache is set only by this library). For arbitrary `CLVMStorage` objects, always compute from content.
3. **Enforce immutability** on `Program` by using `__slots__` or `__setattr__` guards so that `atom` and `_pair` cannot be reassigned after construction.

---

### Proof of Concept

```python
from clvm_rs.clvm_storage import CLVMStorage
from clvm_rs.program import Program

# Attacker-controlled CLVMStorage with forged _cached_sha256_treehash
class ForgedStorage(CLVMStorage):
    atom = b"real_content"
    pair = None
    _cached_sha256_treehash = b"\xde\xad" * 16  # 32 bytes of garbage

forged = ForgedStorage()

# Program.wrap() copies the forged hash unconditionally
p = Program.wrap(forged)

# tree_hash() returns the forged hash, not sha256tree(b"real_content")
print(p.tree_hash().hex())  # prints "deaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddead"

# __eq__ uses tree_hash(), so equality is now based on the forged hash
real = Program.new_atom(b"real_content")
print(real == p)  # False — same content, different hash due to forgery

# A different program with the same forged hash would compare as equal
class ForgedStorage2(CLVMStorage):
    atom = b"different_content"
    pair = None
    _cached_sha256_treehash = b"\xde\xad" * 16  # same forged hash

p2 = Program.wrap(ForgedStorage2())
print(p == p2)  # True — different content, same forged hash
```

The root cause is at `tree_hash.py:57-61` (unconditional cache trust) and `program.py:142` (cache propagation from untrusted source), with the corrupted result surfacing at `program.py:284-286` (`tree_hash()`) and `program.py:188` (`__eq__`). [7](#0-6) [8](#0-7)

### Citations

**File:** wheel/python/clvm_rs/tree_hash.py (L50-61)
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
```

**File:** wheel/python/clvm_rs/program.py (L133-143)
```python
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

**File:** wheel/python/clvm_rs/program.py (L188-188)
```python
        return self.tree_hash() == other_obj.tree_hash()
```

**File:** wheel/python/clvm_rs/program.py (L284-286)
```python
        if self._cached_sha256_treehash is None:
            self._cached_sha256_treehash = sha256_treehash(self._unwrapped)
        return self._cached_sha256_treehash
```

**File:** wheel/python/clvm_rs/program.py (L341-362)
```python
    def curry_hash(self, *args: bytes) -> bytes:
        """
        Return a puzzle hash that would be created if you curried this puzzle
        with arguments that have the given hashes.

        In other words,

        ```
        c1 = self.curry(arg1, arg2, arg3).tree_hash()
        c2 = self.curry_hash(arg1.tree_hash(), arg2.tree_hash(), arg3.tree_hash())
        assert c1 == c2  # they will be the same
        ```

        This looks useless to the unitiated, but sometimes you'll need a puzzle
        hash where you don't actually know the contents of a clvm subtree -- just its
        hash. This lets you calculate the puzzle hash with hidden information.
        """
        curry_treehasher = self.curry_treehasher
        quoted_mod_hash = curry_treehasher.calculate_hash_of_quoted_mod_hash(
            self.tree_hash()
        )
        return curry_treehasher.curry_and_treehash(quoted_mod_hash, *args)
```
