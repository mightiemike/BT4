The exploit path is concrete and traceable through the Python wheel code. Here is the full analysis:

---

### Title
Non-Canonical Serialization Propagated via `CLVMTree._cached_serialization` — (`wheel/python/clvm_rs/clvm_tree.py`, `wheel/python/clvm_rs/ser.py`, `wheel/python/clvm_rs/program.py`)

### Summary

`CLVMTree._cached_serialization` is a `@property` that returns a raw `memoryview` slice of the original input blob with no canonicality check. `sexp_to_byte_iterator` short-circuits on this value and yields it directly. `Program.wrap` copies it into `Program._cached_serialization`. `Program.__bytes__` returns it as-is. The result: a program deserialized from a non-canonical blob (e.g. `\xc0\x00` for nil) will have `bytes(program)` return the non-canonical bytes, not the canonical `\x80`.

### Finding Description

**Step 1 — Parsing accepts non-canonical blobs.**

`CLVMTree.from_bytes` calls `deserialize_as_tuples`, which calls the Rust `deserialize_as_tree` when available, or the Python fallback in `de.py`. The Python `_atom_size_from_cursor` function parses `\xc0\x00` as a zero-length atom (nil) without any canonicality check: [1](#0-0) 

For `\xc0\x00`: `bit_count=2`, `size=0`, `new_cursor=2`. No error is raised. The atom value is correctly `b''`, but the serialized region stored is `blob[0:2]` = `\xc0\x00`.

**Step 2 — `_cached_serialization` returns the raw non-canonical slice.**

`CLVMTree._cached_serialization` is a `@property` that unconditionally returns `self.blob[start:end]`: [2](#0-1) 

For a blob of `\xc0\x00`, this returns `memoryview(b'\xc0\x00')` — the non-canonical encoding.

**Step 3 — `sexp_to_byte_iterator` short-circuits on `_cached_serialization`.** [3](#0-2) 

`getattr(sexp, '_cached_serialization', None)` calls the property, gets the non-canonical memoryview, and yields it directly. `atom_to_byte_iterator` — which would produce canonical `\x80` — is never reached.

**Step 4 — `Program.wrap` propagates the non-canonical cache.** [4](#0-3) 

`getattr(v, '_cached_serialization', None)` calls the property on the `CLVMTree`, storing the non-canonical memoryview into `Program._cached_serialization`.

**Step 5 — `Program.__bytes__` returns non-canonical bytes.** [5](#0-4) 

Since `_cached_serialization` is not `None`, `sexp_to_bytes` is skipped. The memoryview is converted to `bytes`, returning `b'\xc0\x00'` instead of `b'\x80'`.

**Step 6 — `from_bytes_with_cursor` is the production entry point.** [6](#0-5) 

This method uses `CLVMTree.from_bytes` directly (not `deser_auto`), making it the concrete attacker-controlled path. Passing `blob=b'\xc0\x00'` and `cursor=0` returns a `Program` whose `bytes()` is `b'\xc0\x00'`.

**Step 7 — `run_with_cost` passes non-canonical bytes to the Rust executor.** [7](#0-6) 

`prog_bytes = bytes(self)` will be the non-canonical bytes. If `run_serialized_chia_program` (Rust) accepts non-canonical legacy CLVM bytes (which classic CLVM deserializers do), execution proceeds. A validator that checks canonical form before execution will reject the same spend; one that does not will accept it.

### Impact Explanation

The invariant that `bytes(Program)` always returns canonical CLVM bytes is broken. Concretely:

- `bytes(Program.from_bytes_with_cursor(b'\xc0\x00', 0)[0])` returns `b'\xc0\x00'`, not `b'\x80'`.
- `sexp_to_byte_iterator` emits `b'\xc0\x00'` for the same object.
- `atom_to_byte_iterator` would emit `b'\x80'` for the same atom value.
- Callers that validate canonical form (e.g. `is_canonical_serialization`) reject the output; callers that do not accept it. This is the split.
- Tree hashes are unaffected (computed from atom values, not serialized bytes), so `tree_hash()` is consistent — but `bytes()` is not, creating a divergence between hash-based identity and byte-based identity.

### Likelihood Explanation

`Program.from_bytes_with_cursor` is a public production API used to parse CLVM data at a given offset. An attacker who controls the input blob (e.g. a spend bundle) can supply a non-canonical encoding. The Python deserializer has no canonicality guard. The Rust `deserialize_as_tree` is designed to cache the original bytes for fast re-serialization, making it structurally unlikely to reject non-canonical input either.

### Recommendation

1. In `CLVMTree._cached_serialization`, validate that the stored slice is canonical before returning it, or do not expose `_cached_serialization` for non-canonical input.
2. In `sexp_to_byte_iterator`, after retrieving `_cached_serialization`, verify it is canonical (e.g. by checking the first byte against the atom value length), and fall through to `atom_to_byte_iterator` if not.
3. In `deserialize_as_tuples` / `_atom_size_from_cursor`, reject non-canonical size encodings (e.g. `\xc0\x00` for a zero-length atom when `\x80` is the canonical form).
4. Add a canonicality check in `Program.__bytes__` before returning a cached non-`bytes` value.

### Proof of Concept

```python
from clvm_rs.clvm_tree import CLVMTree
from clvm_rs.program import Program

# Non-canonical nil: 0xC0 0x00 encodes a 0-byte atom using a 2-byte prefix.
# Canonical nil is 0x80.
blob = bytes.fromhex("c000")

tree = CLVMTree.from_bytes(blob)
program = Program.wrap(tree)

serialized = bytes(program)
print(f"atom value:   {program.atom!r}")          # b'' (correct)
print(f"bytes(prog):  {serialized.hex()}")         # c000 (non-canonical!)
print(f"canonical nil: 80")

# Invariant violation: bytes(program) != b'\x80'
assert serialized == b"\x80", f"FAIL: got {serialized.hex()}, expected 80"
# This assertion fails, proving the invariant is broken.

# Tree hash is consistent (computed from atom value, not bytes):
print(f"tree_hash: {program.tree_hash().hex()}")   # same as Program.fromhex('80').tree_hash()
assert program.tree_hash() == Program.fromhex("80").tree_hash()  # passes

# But bytes() diverges:
assert bytes(program) == bytes(Program.fromhex("80"))  # FAILS
```

### Citations

**File:** wheel/python/clvm_rs/de.py (L99-119)
```python
def _atom_size_from_cursor(blob, cursor) -> Tuple[int, int]:
    # return `(size_of_prefix, cursor)`
    b = blob[cursor]
    if b == 0x80:
        return 1, cursor + 1
    if b <= MAX_SINGLE_BYTE:
        return 0, cursor + 1
    bit_count = 0
    bit_mask = 0x80
    while b & bit_mask:
        bit_count += 1
        b &= 0xFF ^ bit_mask
        bit_mask >>= 1
    size_blob = bytes([b])
    if bit_count > 1:
        size_blob += blob[cursor + 1:cursor + bit_count]
    size = int.from_bytes(size_blob, "big")
    new_cursor = cursor + size + bit_count
    if new_cursor > len(blob):
        raise ValueError("end of stream")
    return bit_count, new_cursor
```

**File:** wheel/python/clvm_rs/clvm_tree.py (L102-105)
```python
    @property
    def _cached_serialization(self) -> bytes:
        start, end, _ = self.int_tuples[self.index]
        return self.blob[start:end]
```

**File:** wheel/python/clvm_rs/ser.py (L34-37)
```python
        r = getattr(sexp, "_cached_serialization", None)
        if r is not None:
            yield r
            continue
```

**File:** wheel/python/clvm_rs/program.py (L57-63)
```python
    def from_bytes_with_cursor(
        cls, blob: bytes, cursor: int
    ) -> Tuple[Program, int]:
        tree = CLVMTree.from_bytes(blob[cursor:])
        obj = cls.wrap(tree)
        new_cursor = len(bytes(tree)) + cursor
        return obj, new_cursor
```

**File:** wheel/python/clvm_rs/program.py (L87-92)
```python
    def __bytes__(self) -> bytes:
        if self._cached_serialization is None:
            self._cached_serialization = sexp_to_bytes(self)
        if not isinstance(self._cached_serialization, bytes):
            self._cached_serialization = bytes(self._cached_serialization)
        return self._cached_serialization
```

**File:** wheel/python/clvm_rs/program.py (L141-141)
```python
        o._cached_serialization = getattr(v, "_cached_serialization", None)
```

**File:** wheel/python/clvm_rs/program.py (L291-292)
```python
        prog_bytes = bytes(self)
        args_bytes = bytes(self.to(args))
```
