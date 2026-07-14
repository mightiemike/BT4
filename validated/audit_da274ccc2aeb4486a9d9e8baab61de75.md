### Title
Stale `node_map` Entries Not Cleared in `TreeCache::restore()` Enable Incorrect Back-Reference Generation After NodePtr Reuse — (File: `src/serde/tree_cache.rs`)

---

### Summary

`TreeCache::restore()` rolls back the serialization stack and `serialized_nodes` bitset, but leaves stale entries in `node_map`, `node_entries`, `atom_lookup`, and `pair_lookup`. If the caller subsequently restores the `Allocator` to a checkpoint (freeing NodePtr indices) and allocates new nodes that receive the same NodePtr values, `TreeCache::update()` will silently reuse the stale `NodeEntry` — wrong tree hash, wrong serialized length, wrong parent relationships — for the new nodes. The serializer then emits incorrect back-reference paths, producing a byte stream that deserializes to a different CLVM tree than intended.

---

### Finding Description

`TreeCache` maps every visited `NodePtr` to an index in `node_entries`:

```rust
node_map: HashMap<NodePtr, u32>,
```

`TreeCacheCheckpoint` saves only three fields:

```rust
pub struct TreeCacheCheckpoint {
    stack: Vec<u32>,
    serialized_nodes: BitSet,
    sentinel_entry: Option<u32>,
}
```

`TreeCache::restore()` reinstates those three fields but leaves `node_map`, `node_entries`, `atom_lookup`, and `pair_lookup` untouched:

```rust
pub fn restore(&mut self, st: TreeCacheCheckpoint) {
    // adjusts on_stack counters, restores self.stack and self.serialized_nodes
    // re-inserts sentinel_entry into node_map
    // ← node_map entries added after the checkpoint are NEVER removed
}
```

`TreeCache::update()` treats any NodePtr already present in `node_map` as fully processed and skips re-hashing:

```rust
let e = match self.node_map.entry(node) {
    Entry::Occupied(e) => {
        let idx = *e.get();
        stack.push(idx);
        continue;          // ← uses stale NodeEntry, no re-hash
    }
    Entry::Vacant(e) => e,
};
```

**Trigger sequence:**

1. `ser.add(a, tree_A)` — `update()` inserts `NodePtr(Bytes(k)) → NodeEntry(idx=N)` into `node_map` with tree-hash and serialized-length of atom A.
2. `ser.restore(undo_state)` — `stack` and `serialized_nodes` roll back; `node_map[NodePtr(Bytes(k))]` remains.
3. `allocator.restore_checkpoint(cp)` — atom-vec index `k` is freed (truncated).
4. `allocator.new_atom(b"different_content")` — new atom lands at index `k`; NodePtr is again `NodePtr(Bytes(k))`.
5. `ser.add(a, tree_B)` — `update()` hits `Entry::Occupied` for `NodePtr(Bytes(k))`, reuses `NodeEntry(idx=N)` (atom A's hash/length/parents). The new atom is never hashed.
6. `push(NodePtr(Bytes(k)))` marks `NodeEntry(idx=N)` as on-stack and, if its stale `serialized_length ≥ MIN_SERIALIZED_LENGTH`, inserts it into `serialized_nodes`.
7. `find_path(NodePtr(Bytes(k)))` returns a path derived from atom A's parent chain.
8. Serializer emits `0xfe <path>` — a back-reference to atom A's content — instead of serializing atom B inline.
9. Deserialization resolves the back-reference to atom A's bytes; the reconstructed tree differs from tree B.

---

### Impact Explanation

**Impact: High.** The serialized byte stream is structurally valid (parseable) but semantically wrong: back-references resolve to stale content. Any consumer that deserializes the output — including a Chia full node verifying a coin spend — reconstructs a different CLVM program than the one that was serialized. This is a consensus-divergence class bug: two nodes that serialize the same logical program via different code paths (incremental vs. one-shot `node_to_bytes_backrefs`) will produce different bytes and disagree on the program's identity or hash.

---

### Likelihood Explanation

**Likelihood: Low.** The bug requires the caller to:
1. Use `Serializer` with `restore()` (the incremental, rollback-capable path).
2. Also restore the `Allocator` to a checkpoint, freeing the same NodePtr indices.
3. Allocate new nodes that happen to land at the freed indices.
4. Continue serializing with the same `Serializer` instance.

This pattern is exactly what the incremental serializer's `UndoState` API is designed to support, and the fuzz harness (`fuzz/fuzz_targets/incremental_serializer.rs`) exercises `allocator.restore_checkpoint()` in the same loop that drives the serializer — making the combination reachable under fuzzing and in production callers that implement speculative serialization.

---

### Recommendation

`TreeCacheCheckpoint` must also snapshot the current lengths of `node_map` / `node_entries` / `atom_lookup` / `pair_lookup`. On `restore()`, entries added after the checkpoint must be removed. Concretely:

- Record `node_entries.len()` in `TreeCacheCheckpoint`.
- On `restore()`, drain all `node_map` entries whose value (NodeEntry index) `≥ checkpoint.node_entries_len`, truncate `node_entries` to that length, and remove the corresponding keys from `atom_lookup` and `pair_lookup`.

Alternatively, document that the `Allocator` must not be restored to a checkpoint while a `Serializer` is alive, and add a `debug_assert` or version counter to enforce this.

---

### Proof of Concept

```rust
use clvmr::{Allocator, NodePtr};
use clvmr::serde::{Serializer, node_from_bytes_backrefs, node_to_bytes};

let mut a = Allocator::new();
let sentinel = a.new_pair(NodePtr::NIL, NodePtr::NIL).unwrap();

// Step 1: take an allocator checkpoint BEFORE allocating any atoms
let alloc_cp = a.checkpoint();

// Step 2: allocate a large atom A (needs ≥ MIN_SERIALIZED_LENGTH=4 bytes to be back-ref eligible)
let atom_a = a.new_atom(b"AAAA_content").unwrap();  // lands at some index k
let tree_a = a.new_pair(atom_a, sentinel).unwrap();

// Step 3: serialize tree_a; tree_cache.node_map gains NodePtr(Bytes(k)) → NodeEntry
let mut ser = Serializer::new(Some(sentinel));
let (_, undo) = ser.add(&a, tree_a).unwrap();

// Step 4: roll back the serializer (stale node_map entry for NodePtr(Bytes(k)) remains)
ser.restore(undo);

// Step 5: roll back the allocator (frees index k)
a.restore_checkpoint(&alloc_cp);

// Step 6: allocate a DIFFERENT atom B — it lands at the same index k → same NodePtr
let atom_b = a.new_atom(b"BBBB_different").unwrap();  // NodePtr == atom_a's old NodePtr
let tree_b = a.new_pair(atom_b, sentinel).unwrap();

// Step 7: serialize tree_b — update() hits the stale entry, skips hashing atom_b,
//         uses atom_a's NodeEntry; find_path() may emit a back-ref to atom_a's bytes
let (done, _) = ser.add(&a, tree_b).unwrap();
// If done is false, call ser.add(&a, NodePtr::NIL) to terminate.

let output = ser.into_inner();

// Step 8: deserialize and compare — the reconstructed tree contains atom_a's bytes,
//         not atom_b's bytes, demonstrating the stale-cache corruption.
let mut a2 = Allocator::new();
let result = node_from_bytes_backrefs(&mut a2, &output).unwrap();
// node_to_bytes(a2, result) will show "AAAA_content" where "BBBB_different" was expected
```

**Root cause lines:**

- `TreeCacheCheckpoint` missing `node_map`/`node_entries` snapshot: [1](#0-0) 
- `restore()` not clearing stale `node_map` entries: [2](#0-1) 
- `update()` short-circuits on stale `Occupied` entry: [3](#0-2) 
- `Serializer::restore()` delegates to `tree_cache.restore()` without clearing allocator-level state: [4](#0-3) 
- Allocator checkpoint/restore that frees NodePtr indices: [5](#0-4)

### Citations

**File:** src/serde/tree_cache.rs (L80-85)
```rust
#[derive(Clone)]
pub struct TreeCacheCheckpoint {
    stack: Vec<u32>,
    serialized_nodes: BitSet,
    sentinel_entry: Option<u32>,
}
```

**File:** src/serde/tree_cache.rs (L163-180)
```rust
    pub fn restore(&mut self, st: TreeCacheCheckpoint) {
        for idx in &self.stack {
            self.node_entries[*idx as usize].on_stack -= 1;
        }
        for e in &self.node_entries {
            debug_assert_eq!(e.on_stack, 0);
        }

        self.stack = st.stack;
        for idx in &self.stack {
            self.node_entries[*idx as usize].on_stack += 1;
        }
        self.serialized_nodes = st.serialized_nodes;
        if let Some(sentinel_entry) = st.sentinel_entry {
            self.node_map
                .insert(self.sentinel_node.unwrap(), sentinel_entry);
        }
    }
```

**File:** src/serde/tree_cache.rs (L221-231)
```rust
                    let e = match self.node_map.entry(node) {
                        Entry::Occupied(e) => {
                            // If this node is already in the node_map, meaning
                            // we've already traversed it once. No need to do it
                            // again.
                            let idx = *e.get();
                            stack.push(idx);
                            continue;
                        }
                        Entry::Vacant(e) => e,
                    };
```

**File:** src/serde/incremental.rs (L107-115)
```rust
    pub fn restore(&mut self, state: UndoState) {
        self.read_op_stack = state.read_op_stack;
        self.write_stack = state.write_stack;
        self.tree_cache.restore(state.tree_cache);
        self.output.set_position(state.output_position);
        self.output
            .get_mut()
            .truncate(state.output_position as usize);
    }
```

**File:** src/allocator.rs (L485-498)
```rust
    pub fn restore_transparent_checkpoint(&mut self, cp: &TransparentCheckpoint) {
        // if any of these asserts fire, it means we're trying to restore to
        // a state that has already been "long-jumped" passed (via another
        // restore to an earlier state). You can only restore backwards in time,
        // not forwards.
        assert!(self.u8_vec.len() >= cp.u8s as usize);
        assert!(self.pair_vec.len() >= cp.pairs as usize);
        assert!(self.atom_vec.len() >= cp.atoms as usize);
        self.ghost_heap += self.u8_vec.len() - cp.u8s as usize;
        self.ghost_pairs += self.pair_vec.len() - cp.pairs as usize;
        self.ghost_atoms += self.atom_vec.len() - cp.atoms as usize;
        self.u8_vec.truncate(cp.u8s as usize);
        self.pair_vec.truncate(cp.pairs as usize);
        self.atom_vec.truncate(cp.atoms as usize);
```
