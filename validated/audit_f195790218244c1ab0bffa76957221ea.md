The code path is fully traceable. Let me lay out the exact findings.

**Step 1: Deserialization routing** — `DataOrFin<StateDiffChunk>` deserialization in `crates/apollo_protobuf/src/converters/state_diff.rs`: [1](#0-0) 

The branch condition is `declared_class.compiled_class_hash.as_ref()` — it only checks `is_some()`. `Some(Felt::ZERO)` is `Some(...)`, so it routes to `DeclaredClass`, not `DeprecatedDeclaredClass`.

**Step 2: `TryFrom<protobuf::DeclaredClass> for DeclaredClass`** — no zero-value guard: [2](#0-1) 

`Some(ZERO)` passes the `ok_or(missing(...))` check and produces `CompiledClassHash(Felt::ZERO)`.

**Step 3: `unite_state_diffs`** — stores it unconditionally: [3](#0-2) 

No zero-value check. `CompiledClassHash(Felt::ZERO)` is inserted into `class_hash_to_compiled_class_hash`.

**Step 4: `write_to_storage`** — persists to DB: [4](#0-3) 

`append_state_diff` writes `compiled_class_hash_table` entries directly from the `ThinStateDiff`: [5](#0-4) 

**Step 5: No state-diff commitment verification in the p2p sync client**

`parse_data_for_block` only validates `state_diff_length` (count of entries) and duplicate/conflicting parts. It does **not** compute `calculate_state_diff_hash` and compare it against the `state_diff_commitment` stored in the block header: [6](#0-5) 

The `state_diff_commitment` field exists in the stored header and `calculate_state_diff_hash` exists: [7](#0-6) 

But the p2p sync client never calls it. The committer (`apollo_committer`) has `verify_state_diff_hash` but that is a separate component used by the batcher, not by the p2p sync client: [8](#0-7) 

---

**Summary of the attack path:**

A malicious p2p peer sends a `StateDiffsResponse` containing a `DeclaredClass` with `compiled_class_hash = Some(Hash { elements: [0,0,0,0] })` (wire-encoded zero). The receiving node:

1. Routes it to `StateDiffChunk::DeclaredClass` (presence check passes)
2. Constructs `DeclaredClass { compiled_class_hash: CompiledClassHash(Felt::ZERO) }` (no zero guard)
3. Inserts it into `class_hash_to_compiled_class_hash` (no zero guard)
4. Persists it to the DB (no state-diff commitment cross-check)

The node now has `class_hash → CompiledClassHash(ZERO)` in storage. Any subsequent CASM lookup for that class hash will use hash `ZERO`, which either finds no CASM (execution error) or, if a CASM with hash ZERO were somehow present, selects wrong code.

---

### Title
Missing zero-value guard on `compiled_class_hash` in p2p state-diff deserialization allows malicious peer to store `CompiledClassHash(ZERO)` — (`crates/apollo_protobuf/src/converters/state_diff.rs`)

### Summary
The p2p sync client's `DataOrFin<StateDiffChunk>` deserialization classifies a `protobuf::DeclaredClass` as a Cairo-1 class solely by checking `compiled_class_hash.is_some()`. A zero-valued `Some(ZERO)` passes this check, passes `TryFrom<protobuf::DeclaredClass> for DeclaredClass` (no zero guard), and is stored verbatim in `class_hash_to_compiled_class_hash`. No state-diff commitment verification is performed against the `state_diff_commitment` in the block header, so the wrong value is never caught.

### Finding Description
- **Routing check** (`state_diff.rs:53`): `declared_class.compiled_class_hash.as_ref()` — only tests `Option::is_some()`, not the inner value.
- **Conversion** (`state_diff.rs:265-270`): `TryFrom<protobuf::DeclaredClass> for DeclaredClass` extracts the felt and wraps it in `CompiledClassHash(...)` with no non-zero assertion.
- **Accumulation** (`state_diff.rs:164-172`): `unite_state_diffs` inserts `CompiledClassHash(Felt::ZERO)` into `ThinStateDiff::class_hash_to_compiled_class_hash`.
- **Persistence** (`state_diff.rs:34`): `append_state_diff` writes the entry to the `compiled_class_hash` LMDB table.
- **Missing cross-check**: `parse_data_for_block` never computes `calculate_state_diff_hash` and compares it against the header's `state_diff_commitment`.

### Impact Explanation
The node stores `class_hash → CompiledClassHash(ZERO)` for a Cairo-1 class. All subsequent CASM/native-artifact lookups keyed by that compiled class hash will fail or select wrong code, corrupting execution results for any transaction that invokes the affected class. This diverges the node's state from the canonical chain and causes RPC execution, fee estimation, and simulation to return wrong values.

### Likelihood Explanation
Any peer the node connects to for state-diff sync can trigger this. No authentication or privilege is required. The attacker only needs to be accepted as a p2p peer and respond to a `StateDiffsRequest`.

### Recommendation
1. Add a non-zero check in `TryFrom<protobuf::DeclaredClass> for DeclaredClass`:
   ```rust
   if compiled_class_hash_felt == Felt::ZERO {
       return Err(ProtobufConversionError::ZeroCompiledClassHash);
   }
   ```
2. In `parse_data_for_block`, after assembling the full `ThinStateDiff`, compute `calculate_state_diff_hash(&result)` and compare it against `block_header.state_diff_commitment`. Reject the peer if they differ.

### Proof of Concept
```rust
use apollo_protobuf::protobuf::{DeclaredClass, Felt252, Hash, StateDiffsResponse,
    state_diffs_response::StateDiffMessage};
use apollo_protobuf::sync::{DataOrFin, StateDiffChunk};
use starknet_types_core::felt::Felt;

let zero_hash = Hash { elements: vec![0u8; 32] };
let proto = StateDiffsResponse {
    state_diff_message: Some(StateDiffMessage::DeclaredClass(DeclaredClass {
        class_hash: Some(Hash { elements: vec![1u8; 32] }),
        compiled_class_hash: Some(zero_hash),
    })),
};
let result: DataOrFin<StateDiffChunk> = proto.try_into().unwrap();
match result.0.unwrap() {
    StateDiffChunk::DeclaredClass(dc) => {
        assert_eq!(dc.compiled_class_hash.0, Felt::ZERO); // passes, zero stored
    }
    _ => panic!("expected DeclaredClass"),
}
```

### Citations

**File:** crates/apollo_protobuf/src/converters/state_diff.rs (L51-60)
```rust
            Some(protobuf::state_diffs_response::StateDiffMessage::DeclaredClass(
                declared_class,
            )) => match declared_class.compiled_class_hash.as_ref() {
                Some(_compiled_class_hash) => {
                    Ok(DataOrFin(Some(StateDiffChunk::DeclaredClass(declared_class.try_into()?))))
                }
                None => Ok(DataOrFin(Some(StateDiffChunk::DeprecatedDeclaredClass(
                    declared_class.try_into()?,
                )))),
            },
```

**File:** crates/apollo_protobuf/src/converters/state_diff.rs (L260-273)
```rust
impl TryFrom<protobuf::DeclaredClass> for DeclaredClass {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclaredClass) -> Result<Self, Self::Error> {
        let class_hash =
            ClassHash(value.class_hash.ok_or(missing("DeclaredClass::class_hash"))?.try_into()?);
        let compiled_class_hash = CompiledClassHash(
            value
                .compiled_class_hash
                .ok_or(missing("DeclaredClass::compiled_class_hash"))?
                .try_into()?,
        );
        Ok(DeclaredClass { class_hash, compiled_class_hash })
    }
}
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L33-35)
```rust
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L58-107)
```rust
        async move {
            let mut result = ThinStateDiff::default();
            let mut prev_result_len = 0;
            let mut current_state_diff_len = 0;
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;

            while current_state_diff_len < target_state_diff_len {
                let maybe_state_diff_chunk = state_diff_chunks_response_manager
                    .next()
                    .await
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
                let Some(state_diff_chunk) = maybe_state_diff_chunk?.0 else {
                    if current_state_diff_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                            expected_length: target_state_diff_len,
                            possible_lengths: vec![current_state_diff_len],
                        }));
                    }
                };
                prev_result_len = current_state_diff_len;
                if state_diff_chunk.is_empty() {
                    return Err(ParseDataError::BadPeer(BadPeerError::EmptyStateDiffPart));
                }
                // It's cheaper to calculate the length of `state_diff_part` than the length of
                // `result`.
                current_state_diff_len += state_diff_chunk.len();
                unite_state_diffs(&mut result, state_diff_chunk)?;
            }

            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L164-172)
```rust
        StateDiffChunk::DeclaredClass(declared_class) => {
            if state_diff
                .class_hash_to_compiled_class_hash
                .insert(declared_class.class_hash, declared_class.compiled_class_hash)
                .is_some()
            {
                return Err(BadPeerError::ConflictingStateDiffParts);
            }
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L645-650)
```rust
        write_compiled_class_hashes(
            &thin_state_diff.class_hash_to_compiled_class_hash,
            inner_txn,
            block_number,
            &compiled_class_hash_table,
        )?;
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-42)
```rust
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    let mut hash_chain = HashChain::new();
    hash_chain = hash_chain.chain(&STARKNET_STATE_DIFF0);
    hash_chain = chain_deployed_contracts(&state_diff.deployed_contracts, hash_chain);
    hash_chain = chain_declared_classes(&state_diff.class_hash_to_compiled_class_hash, hash_chain);
    hash_chain =
        chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
    hash_chain = hash_chain.chain(&Felt::ONE) // placeholder.
        .chain(&Felt::ZERO); // placeholder.
    hash_chain = chain_storage_diffs(&state_diff.storage_diffs, hash_chain);
    hash_chain = chain_nonces(&state_diff.nonces, hash_chain);
    StateDiffCommitment(PoseidonHash(hash_chain.get_poseidon_hash()))
}
```

**File:** crates/apollo_committer/src/committer.rs (L265-280)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(state_diff);
                    if commitment != calculated_commitment {
                        return Err(CommitterError::StateDiffHashMismatch {
                            provided_commitment: commitment,
                            calculated_commitment,
                            height,
                        });
                    }
                }
                commitment
            }
            None => calculate_state_diff_hash(state_diff),
        };
```
