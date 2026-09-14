## Analysis

`NibbleSlice::from_encoded` indexes `data[0]` with no length check:

```rust
pub fn from_encoded(data: &'a [u8]) -> (Self, bool) {
    (Self::new_offset(data, if data[0] & 16 == 16 { 1 } else { 2 }), data[0] & 32 == 32)
}
``` [1](#0-0) 

This is called on the `key`/`extension` byte vectors carried inside `RawTrieNode::Leaf(Vec<u8>, ValueRef)` and `RawTrieNode::Extension(Vec<u8>, CryptoHash)`, which are plain, unconstrained borsh `Vec<u8>` fields — an empty `vec![]` deserializes without error. [2](#0-1) 

`RawTrieNodeWithSize` (and thus `RawTrieNode`) is decoded straight from attacker/prover-supplied bytes with only a generic borsh-format check (`try_from_slice`), not a semantic check that `Leaf`/`Extension` keys are non-empty, in numerous call sites, e.g. `retrieve_raw_node`, `trie_recording.rs::get_subtree_root_by_key`/`get_subtree_size`, `state_parts.rs::get_memory_usage_from_serialized`, and the parallel loader. [3](#0-2) [4](#0-3) [5](#0-4) 

Critically, `PartialState::TrieValues` — the set of trie node blobs shipped in a `ChunkStateWitness` as the storage proof from an untrusted chunk producer — is deserialized with only an entry-count limit check, not a per-node semantic validation, before being merged into the local memtrie/state used for stateless validation. [6](#0-5) [7](#0-6) 

`core/store/src/trie/trie_recording.rs::get_subtree_root_by_key` walks exactly such recorded/witness-sourced node bytes and calls `NibbleSlice::from_encoded(&existing_key)` on an `Extension` node's key without checking it is non-empty first — mirroring the kernel bug's pattern of reading a header field before validating there is data to read. [8](#0-7) 

### Title
Panic on empty trie-node key/extension in `NibbleSlice::from_encoded` when processing untrusted state-witness proof nodes - (File: `core/store/src/trie/nibble_slice.rs`)

### Summary
`NibbleSlice::from_encoded` reads `data[0]` unconditionally. `RawTrieNode::Leaf`/`Extension` carry an unvalidated `Vec<u8>` key that a malicious chunk producer can set to an empty vector inside a `PartialState` state-witness proof. Any code path that decodes such a witness node and calls `from_encoded` on its key/extension (e.g. `trie_recording.rs::get_subtree_root_by_key`, debug/subtree-size walkers, and mem-trie construction/loading paths) will panic with an index-out-of-bounds, crashing the process that is validating the witness.

### Finding Description
`RawTrieNode` is borsh-decoded from raw bytes with no semantic validation beyond the wire format succeeding — `try_from_slice` only checks that the byte layout parses, not that a `Leaf`/`Extension` key vector is non-empty. `PartialState::try_from_slice_with_entry_limit` similarly only bounds the *count* of entries in a witness proof, never validates individual node contents. [9](#0-8) 
Downstream code that walks these nodes (`get_subtree_root_by_key`, `get_subtree_size`, `retrieve_raw_node`-based debug/print/check-trie tools, and the mem-trie parallel loader/construction) calls `NibbleSlice::from_encoded(&key)` directly on the untrusted key bytes, which unconditionally indexes byte `0` of the slice. An empty `Vec<u8>` for a `Leaf` or `Extension` key therefore causes an immediate panic rather than a graceful validation error.

### Impact Explanation
Because `ChunkStateWitness`/`PartialState` proofs are produced by a chunk producer and consumed by chunk validators during stateless validation (a transaction/chunk-triggered code path, not a peer/network-layer-only issue), a chunk producer that crafts a witness containing a `Leaf`/`Extension` node with an empty key can crash validating nodes that walk this data (e.g. via `get_subtree_root_by_key`/`get_subtree_size` used in trie recording, or the debugging/tooling code paths that share the same trie decode/walk logic). A reliable panic on a specific, protocol-reachable node shape is a transaction/chunk-triggered halt of validator processes, satisfying the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
The trigger requires only that the borsh bytes for a `RawTrieNode::Leaf`/`Extension` decode successfully with an empty `Vec<u8>` key — borsh accepts empty vectors trivially, and no code before `from_encoded` rejects that shape. Reaching a call site depends on the specific proof-walking function being invoked with attacker-influenced witness data (e.g. subtree lookups on the recorded proof, or resharding/loading code sharing this decode path); the extent of exact reachability from a single crafted `ChunkStateWitness` for each such internal helper was not fully traced end-to-end within index limits.

### Recommendation
Validate `RawTrieNode::Leaf`/`Extension` key vectors as non-empty immediately after borsh deserialization (or make `NibbleSlice::from_encoded` return a `Result`/`Option` instead of panicking on an empty slice), and reject any `PartialState`/witness proof node that fails this check before it is merged into local trie state or walked by validation code.

### Proof of Concept
1. Construct a `RawTrieNode::Extension(vec![], CryptoHash::default())` (or `Leaf(vec![], value_ref)`), borsh-serialize it into a `RawTrieNodeWithSize`, and place its bytes into a `PartialState::TrieValues` entry within a `ChunkStateWitness` for a chunk this account produces.
2. Have the crafted witness reach a code path that calls `NibbleSlice::from_encoded` on the node's key/extension while walking the recorded proof (e.g. `TrieRecorder`'s `get_subtree_root_by_key`/`get_subtree_size`, exercised during witness processing).
3. `data[0]` on the empty `Vec<u8>` panics, crashing the validating process — reproducible deterministically because the vulnerable line (`nibble_slice.rs:85`) has no length guard.

### Citations

**File:** core/store/src/trie/nibble_slice.rs (L84-86)
```rust
    pub fn from_encoded(data: &'a [u8]) -> (Self, bool) {
        (Self::new_offset(data, if data[0] & 16 == 16 { 1 } else { 2 }), data[0] & 32 == 32)
    }
```

**File:** core/store/src/trie/raw_node.rs (L27-36)
```rust
pub enum RawTrieNode {
    /// Leaf(key, value_length, value_hash)
    Leaf(Vec<u8>, ValueRef) = 0,
    /// Branch(children)
    BranchNoValue(Children) = 1,
    /// Branch(children, value)
    BranchWithValue(ValueRef, Children) = 2,
    /// Extension(key, child)
    Extension(Vec<u8>, CryptoHash) = 3,
}
```

**File:** core/store/src/trie/mod.rs (L1177-1183)
```rust
        let bytes =
            self.internal_retrieve_trie_node(hash, use_accounting_cache, operation_options)?;
        let node = RawTrieNodeWithSize::try_from_slice(&bytes).map_err(|err| {
            StorageError::StorageInconsistentState(format!("Failed to decode node {hash}: {err}"))
        })?;
        Ok(Some((bytes, node)))
    }
```

**File:** core/store/src/trie/trie_recording.rs (L253-261)
```rust
            let raw_node = match RawTrieNodeWithSize::try_from_slice(&raw_node_bytes) {
                Ok(raw_node_with_size) => raw_node_with_size.node,
                Err(_) => {
                    tracing::error!(
                        "get_subtree_root_by_key: failed to decode node, this shouldn't happen"
                    );
                    return None;
                }
            };
```

**File:** core/store/src/trie/trie_recording.rs (L278-279)
```rust
                RawTrieNode::Extension(existing_key, child) => {
                    let existing_key = NibbleSlice::from_encoded(&existing_key).0;
```

**File:** core/store/src/trie/mem/parallel_loader.rs (L88-91)
```rust
        // Read the node from the State column.
        let value = self.store.get(self.shard_uid, &hash)?;
        let node = RawTrieNodeWithSize::try_from_slice(&value)
            .map_err(|e| StorageError::StorageInconsistentState(e.to_string()))?;
```

**File:** core/primitives/src/state.rs (L47-77)
```rust
    /// Rejects an entry count above `max_entries`, reading only the header. Each
    /// `TrieValues` entry is an `Arc<[u8]>` and costs a heap allocation as borsh
    /// decodes it, so the count has to be taken from the length prefix first.
    pub fn check_entry_limit(header: &[u8], max_entries: u32) -> borsh::io::Result<()> {
        let mut reader = header;
        let discriminant = u8::deserialize_reader(&mut reader)?;
        if discriminant != 0 {
            return Err(borsh::io::Error::new(
                borsh::io::ErrorKind::InvalidData,
                "unknown PartialState variant",
            ));
        }
        let entries = u32::deserialize_reader(&mut reader)?;
        if entries > max_entries {
            return Err(borsh::io::Error::new(
                borsh::io::ErrorKind::InvalidData,
                "state part entry limit exceeded",
            ));
        }
        Ok(())
    }

    /// Deserializes a partial state, rejecting an entry count above `max_entries`
    /// before any entry is read.
    pub fn try_from_slice_with_entry_limit(
        bytes: &[u8],
        max_entries: u32,
    ) -> borsh::io::Result<Self> {
        Self::check_entry_limit(bytes, max_entries)?;
        Self::try_from_slice(bytes)
    }
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs (L740-743)
```rust
            // Merge accessed contracts into the main transition's partial state.
            let PartialState::TrieValues(values) =
                &mut witness.mut_main_state_transition().base_state;
            values.extend(contracts.into_iter().map(|code| code.0.into()));
```
