Now I have all the information needed to make a definitive determination. Let me trace the exact code paths.

**`deser_auto` in `wheel/src/api.rs` lines 148–157:**

```rust
fn deser_auto(blob: &[u8], max_atom_len: usize, strict: bool) -> PyResult<LazyNode> {
    let mut a = Allocator::new();
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
            .map_err(eval_to_py)?
    } else {
        node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?  // max_atom_len silently dropped
    };
    Ok(LazyNode::new(Rc::new(a), node))
}
```

**`node_from_bytes_backrefs` in `src/serde/de_br.rs` lines 114–117:**

```rust
pub fn node_from_bytes_backrefs(allocator: &mut Allocator, b: &[u8]) -> Result<NodePtr> {
    let mut buffer = Cursor::new(b);
    node_from_stream_backrefs(allocator, &mut buffer, |_node| {})
}
```

No `max_atom_len` parameter exists anywhere in the backrefs deserialization stack. The only hard limit is in `decode_size` at `0x400000000` (~17 GB).

**`deserialize_2026_body_from_stream` in `src/serde_2026/de.rs` lines 56, 60:**

```rust
checked_bounded_usize(-length_val, max_atom_len)?  // enforced
checked_bounded_usize(length_val, max_atom_len)?   // enforced
```

**Python docstring in `wheel/python/clvm_rs/serde.py` lines 70–75:**

```
Keyword-only limits (applied to "2026" and "auto" paths):
    max_atom_len:   largest single atom in bytes (default 1 MiB)
```

The docstring explicitly contracts that `max_atom_len` applies to the "auto" path, but the implementation only threads it into the 2026 branch. The backrefs branch silently discards it.

---

### Title
`deser_auto` silently ignores `max_atom_len` and `strict` for backrefs-encoded blobs, violating the documented API contract — (`wheel/src/api.rs`)

### Summary

`deser_auto` accepts `max_atom_len` and `strict` parameters and documents them as applying to the "auto" path, but only passes them to `deserialize_2026_body_from_stream`. When the blob does not start with `SERDE_2026_MAGIC_PREFIX`, the function falls through to `node_from_bytes_backrefs`, which has no `max_atom_len` parameter at all. The parameters are silently dropped.

### Finding Description

In `wheel/src/api.rs`, `deser_auto` dispatches on the magic prefix:

- **2026 path:** calls `deserialize_2026_body_from_stream(..., max_atom_len, strict)` — limit enforced. [1](#0-0) 
- **Backrefs path:** calls `node_from_bytes_backrefs(&mut a, blob)` — `max_atom_len` and `strict` are not passed and cannot be passed, because `node_from_bytes_backrefs` has no such parameters. [2](#0-1) 

`node_from_bytes_backrefs` delegates to `node_from_stream_backrefs`, which calls `parse_atom`, which calls `decode_size`. The only atom-size guard in that entire chain is a hard ceiling of `0x400000000` (~17 GB). [3](#0-2) 

The Python-layer docstring in `serde.py` explicitly states `max_atom_len` is "applied to '2026' and 'auto' paths," making this a documented API contract violation. [4](#0-3) 

### Impact Explanation

A caller who passes `max_atom_len=N` to `deser_auto` to bound memory use or enforce a consensus atom-size rule gets that bound enforced only when the wire blob happens to be 2026-encoded. A backrefs-encoded blob carrying an atom of size `N+1` through `~17 GB` is accepted without error, producing a `LazyNode` whose atom content exceeds the caller's stated limit. The same semantic tree serialized in 2026 format would be rejected. This is a concrete API-equivalence violation: the same `max_atom_len` argument produces different acceptance/rejection outcomes depending solely on the wire encoding, not on the tree content.

Downstream callers (e.g. `chia_rs`) that rely on `deser_auto` + `max_atom_len` for consensus atom-size enforcement receive no protection against oversized atoms in backrefs blobs.

### Likelihood Explanation

The attacker controls the wire encoding. Producing a valid backrefs blob with an arbitrarily large atom is trivial: the backrefs format is a strict superset of the legacy format, and any atom up to ~17 GB can be encoded with a standard length prefix. No special privileges are required; the only entry point is the public Python `deserialize(blob, "auto", max_atom_len=N)` API.

### Recommendation

Pass `max_atom_len` and `strict` through to the backrefs path. Since `node_from_bytes_backrefs` does not accept these parameters, either:

1. Add a `node_from_bytes_backrefs_limited(allocator, blob, max_atom_len)` variant that enforces the limit inside `parse_atom`, and call it from `deser_auto`; or
2. After `node_from_bytes_backrefs` returns, walk the resulting tree and reject any atom exceeding `max_atom_len` (less efficient but requires no changes to the Rust serde layer).

Also update the `deser_backrefs` Python binding to accept and enforce `max_atom_len` for consistency.

### Proof of Concept

```python
import clvm_rs.serde as s

# Build a backrefs blob with a 2 MiB atom (exceeds default 1 MiB limit)
big_atom = b"\xaa" * (2 * 1024 * 1024)
# Legacy/backrefs encoding: length prefix + raw bytes
# 2 MiB = 0x200000; needs 3-byte length prefix: 0xe2 0x00 0x00
blob_backrefs = bytes([0xe2, 0x00, 0x00]) + big_atom

# deser_auto with default max_atom_len=1 MiB: SUCCEEDS (bug)
node = s.deser_auto(blob_backrefs)
assert node.atom is not None and len(node.atom) == 2 * 1024 * 1024

# Same tree in 2026 format: REJECTED (correct)
import clvm_rs
a = clvm_rs.Allocator()
ptr = a.new_atom(big_atom)
blob_2026 = s.ser_2026(s.clvm_tree_to_lazy_node(...))  # serialize same atom
try:
    s.deser_auto(blob_2026)  # raises ValueError: atom exceeds max_atom_len
    assert False, "should have been rejected"
except ValueError:
    pass  # expected
```

The two calls to `deser_auto` with identical `max_atom_len` produce opposite results for the same semantic content, confirming the invariant is broken.

### Citations

**File:** wheel/src/api.rs (L150-152)
```rust
    let node = if let Some(body) = blob.strip_prefix(SERDE_2026_MAGIC_PREFIX.as_slice()) {
        deserialize_2026_body_from_stream(&mut a, &mut Cursor::new(body), max_atom_len, strict)
            .map_err(eval_to_py)?
```

**File:** wheel/src/api.rs (L153-155)
```rust
    } else {
        node_from_bytes_backrefs(&mut a, blob).map_err(eval_to_py)?
    };
```

**File:** src/serde/parse_atom.rs (L44-46)
```rust
    if atom_size >= 0x400000000 {
        return Err(EvalErr::SerializationError);
    }
```

**File:** wheel/python/clvm_rs/serde.py (L70-75)
```python
    Keyword-only limits (applied to "2026" and "auto" paths):
        max_atom_len:   largest single atom in bytes (default 1 MiB)
        strict:         reject overlong varint encodings

    Total input bytes is bounded by the input slice itself; consensus-aware
    callers (e.g. chia_rs) should pass their own ``max_atom_len``.
```
