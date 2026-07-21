### Title
Malicious P2P Peer Can Misclassify Sierra Classes as Deprecated Cairo-0 Classes via `compiled_class_hash=None` Discriminator with No State Diff Hash Validation - (`crates/apollo_protobuf/src/converters/state_diff.rs`)

### Summary

The `TryFrom<protobuf::StateDiffsResponse> for DataOrFin<StateDiffChunk>` implementation uses the sole presence of `compiled_class_hash` to discriminate between `StateDiffChunk::DeclaredClass` (Sierra/Cairo 1.0) and `StateDiffChunk::DeprecatedDeclaredClass` (Cairo 0). A malicious peer can send a `protobuf::DeclaredClass` with `compiled_class_hash=None` for a Sierra class. The p2p sync client (`parse_data_for_block`) validates only `state_diff_length` from the block header — it never reads or checks `state_diff_commitment`. Both variants contribute exactly 1 to the length, so the length check passes, the wrong class type is stored, and the state diff hash silently diverges from the committed value.

### Finding Description

**Discriminator (intentional by spec, exploitable by peer):**

In `crates/apollo_protobuf/src/converters/state_diff.rs` lines 51–60, the `DeclaredClass` vs `DeprecatedDeclaredClass` branch is decided solely by `compiled_class_hash.as_ref()`:

```rust
None => Ok(DataOrFin(Some(StateDiffChunk::DeprecatedDeclaredClass(
    declared_class.try_into()?,
)))),
``` [1](#0-0) 

This is documented as intentional per the p2p spec (comment at lines 148–149). The `TryFrom<protobuf::DeclaredClass> for DeclaredClass` impl (lines 260–272) requires `compiled_class_hash` to be `Some` and returns an error if it is `None`. The `TryFrom<protobuf::DeclaredClass> for DeprecatedDeclaredClass` impl (lines 284–293) ignores `compiled_class_hash` entirely. So a peer that sends `compiled_class_hash=None` for a Sierra class will always produce `DeprecatedDeclaredClass` — no error, no rejection. [2](#0-1) 

**Length check passes — both variants count as 1:**

`StateDiffChunk::len()` returns 1 for both `DeclaredClass` and `DeprecatedDeclaredClass`: [3](#0-2) 

So swapping a `DeclaredClass` for a `DeprecatedDeclaredClass` does not change the total length, and the `target_state_diff_len` check in `parse_data_for_block` passes silently.

**`state_diff_commitment` is never read in the p2p sync path:**

`parse_data_for_block` reads only `state_diff_length` from the stored block header (line 66). It never reads `state_diff_commitment`. After assembling the `ThinStateDiff`, the only post-assembly check is `validate_deprecated_declared_classes_non_conflicting` (line 106), which only checks for duplicate class hashes in `deprecated_declared_classes`. The assembled diff is then written directly to storage via `append_state_diff` with no hash comparison. [4](#0-3) 

**The state diff hash is structurally sensitive to this substitution:**

`calculate_state_diff_hash` chains `class_hash_to_compiled_class_hash` and `deprecated_declared_classes` in separate, non-interchangeable positions of the Poseidon hash: [5](#0-4) 

Moving a class hash from `class_hash_to_compiled_class_hash` (which also chains `compiled_class_hash`) to `deprecated_declared_classes` produces a completely different `StateDiffCommitment`. The block header already stores the correct `state_diff_commitment`, but the p2p sync client never compares against it.

**Storage effect:**

`unite_state_diffs` routes `DeclaredClass` into `class_hash_to_compiled_class_hash` and `DeprecatedDeclaredClass` into `deprecated_declared_classes`: [6](#0-5) 

After the attack, the class hash is in `deprecated_declared_classes` with no `compiled_class_hash` entry, and the stored `ThinStateDiff` silently diverges from the `state_diff_commitment` in the block header.

### Impact Explanation

A syncing node that accepts state diffs from a malicious peer will store a Sierra class as a deprecated Cairo-0 class. Downstream effects:

- `class_hash_to_compiled_class_hash` will be missing the entry for the class, so any lookup for the compiled class hash will fail or return nothing.
- The class will be treated as a deprecated class for execution purposes, causing wrong compiled class selection or execution failure for any transaction that invokes it.
- The stored `ThinStateDiff` will have a hash that does not match the `state_diff_commitment` in the block header, silently corrupting the node's view of canonical state.

This matches: **Critical — Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution.**

### Likelihood Explanation

Any node that connects to the syncing node as a p2p peer can perform this attack. No special privileges are required. The attack requires only that the malicious peer respond to a `StateDiffsRequest` with a `DeclaredClass` message where `compiled_class_hash` is omitted. The protobuf message is structurally valid; no deserialization error is raised. The length check passes. The attack is silent and leaves no error trace.

### Recommendation

In `parse_data_for_block`, after assembling the `ThinStateDiff` and before writing to storage, read `state_diff_commitment` from the stored block header and compare it against `calculate_state_diff_hash(&result)`. Reject the peer (return `BadPeerError`) if the hashes do not match. This is the same pattern already used in `apollo_committer` (`verify_state_diff_hash` config flag), but it is absent from the p2p sync client path.

### Proof of Concept

```rust
// Construct a protobuf::DeclaredClass for a Sierra class but omit compiled_class_hash.
let proto_msg = protobuf::StateDiffsResponse {
    state_diff_message: Some(
        protobuf::state_diffs_response::StateDiffMessage::DeclaredClass(
            protobuf::DeclaredClass {
                class_hash: Some(sierra_class_hash.into()),
                compiled_class_hash: None,  // attacker omits this
            }
        )
    ),
};

// Deserialize via the production path.
let result = DataOrFin::<StateDiffChunk>::try_from(proto_msg).unwrap();

// Assert: variant is DeprecatedDeclaredClass, not DeclaredClass.
assert!(matches!(result.0, Some(StateDiffChunk::DeprecatedDeclaredClass(_))));

// Confirm: class_hash_to_compiled_class_hash is empty; deprecated_declared_classes has the hash.
// The state diff hash will not match the block header's state_diff_commitment.
```

The discriminator is solely the presence of `compiled_class_hash`. No `starknet_version` gate exists in this path. The substitution is undetected because `parse_data_for_block` reads `state_diff_length` but never `state_diff_commitment` from the block header.

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

**File:** crates/apollo_protobuf/src/converters/state_diff.rs (L260-293)
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

impl From<DeclaredClass> for protobuf::DeclaredClass {
    fn from(value: DeclaredClass) -> Self {
        protobuf::DeclaredClass {
            class_hash: Some(value.class_hash.0.into()),
            compiled_class_hash: Some(value.compiled_class_hash.0.into()),
        }
    }
}

impl TryFrom<protobuf::DeclaredClass> for DeprecatedDeclaredClass {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclaredClass) -> Result<Self, Self::Error> {
        Ok(DeprecatedDeclaredClass {
            class_hash: ClassHash(
                value.class_hash.ok_or(missing("DeclaredClass::class_hash"))?.try_into()?,
            ),
        })
    }
}
```

**File:** crates/apollo_protobuf/src/sync.rs (L159-160)
```rust
            StateDiffChunk::DeclaredClass(_) => 1,
            StateDiffChunk::DeprecatedDeclaredClass(_) => 1,
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-107)
```rust
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L164-175)
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
        StateDiffChunk::DeprecatedDeclaredClass(deprecated_declared_class) => {
            state_diff.deprecated_declared_classes.push(deprecated_declared_class.class_hash);
        }
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
