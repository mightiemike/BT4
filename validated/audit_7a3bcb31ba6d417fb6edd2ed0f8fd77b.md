This confirms the key fact: `wtxid` is computed via `calculate_wtxid(tx) = SHA256d(consensus_encode(tx))` — a cryptographic hash over the transaction's full bytes, including its witness/inscription content, and `BitcoinVerifier::verify_transactions` enforces `calculate_wtxid(tx) == wtxid` before ever constructing a `BlobWithSender`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Analysis of the claimed binding

The binding to validate is: `ChunkAccessor::<S>::get(wtxid)` == "the chunk actually produced by `batch_prover_da_public_key` for that `wtxid`."

Tracing `LightClientProofCircuit::run_l1_block`, chunks are unconditionally inserted keyed by `blob.wtxid()` with no sender check, and later an `Aggregate` (whose *outer* sender-signature is checked) looks up each referenced `wtxid` via `ChunkAccessor::<S>::get` and concatenates the bodies. [4](#0-3) [5](#0-4) 

The premise of the attack is that an attacker can insert an arbitrary `DataOnDa::Chunk(attacker_bytes)` under "the exact wtxid the batch prover intended to use next." But `wtxid` is not a free-form field the attacker controls — it is a SHA256d hash of the *entire* consensus-encoded transaction, whose witness script embeds the pushed chunk bytes themselves (see the reveal-script builder pushing `borsh(DataOnDa::Chunk(chunk))` bytes into the script). [6](#0-5) 

For an attacker to make `ChunkAccessor::get(wtxid)` return their `attacker_bytes` instead of the batch prover's real chunk for the *same* `wtxid` that the real `Aggregate` will reference, they would need to construct a distinct Bitcoin transaction (different chunk payload, different or no signature/witness) that nonetheless hashes to the identical `wtxid` as the batch prover's real, not-yet-broadcast chunk transaction. That is a SHA256d preimage/second-preimage requirement on a value the attacker does not know in advance (it depends on the batch prover's private signing key, its chosen UTXOs, nonce, and the exact chunk bytes) — computationally infeasible, not merely "unprivileged but costly." This is exactly the security property the code comment relies on: "No need to check sender for chunk" is safe *because* the wtxid is a content-binding commitment, and the honest `Aggregate`'s signed `wtxids` list only ever points to wtxids of transactions the batch prover itself authored. An attacker inserting `Chunk` data under wtxids of their own making cannot collide with those wtxids without breaking Bitcoin's hash security assumption (out of scope — "compromised dependencies," hash breaks aren't a code defect here).

The equality holds before and after the described attack: `ChunkAccessor::get(wtxid)` for any `wtxid` that a legitimate `Aggregate` from `batch_prover_da_public_key` references will always be the chunk from the transaction that actually produced that exact `wtxid`, because `wtxid` cannot be forged for different content without a hash collision. `BitcoinVerifier::verify_transactions` further pins `calculate_wtxid(tx) == wtxid` per relevant transaction, so there's no way to smuggle a mismatched wtxid/body pair even at the parsing layer.

#No vulnerability found for this question.

### Citations

**File:** crates/bitcoin-da/src/helpers/mod.rs (L117-128)
```rust
/// Computes the segwit version of the transaction id.
///
/// Hashes the transaction **including** all segwit data (i.e. the marker, flag bytes, and the
/// witness fields themselves). For non-segwit transactions which do not have any segwit data,
/// this will be equal to [`Transaction::txid()`].
///
/// To override `Transaction::compute_wtxid` with the patched sha256 impl.
pub fn calculate_wtxid(tx: &Transaction) -> [u8; 32] {
    let mut enc = vec![];
    tx.consensus_encode(&mut enc).expect("engines don't error");
    calculate_double_sha256(&enc)
}
```

**File:** crates/bitcoin-da/src/verifier.rs (L112-116)
```rust
        for (wtxid, tx) in relevant_wtxid_iter.zip_eq(&completeness_proof) {
            // ensure completeness proof tx matches the inclusion tx
            if &calculate_wtxid(tx) != wtxid {
                return Err(ValidationError::RelevantTxNotInProof);
            }
```

**File:** crates/bitcoin-da/src/verifier.rs (L144-152)
```rust
                    ParsedTransaction::Chunk(chunk) => {
                        blobs.push(BlobWithSender::new(
                            chunk.body,
                            // chunk sender and hash irrelevant
                            vec![],
                            [0; 32],
                            *wtxid,
                        ));
                    }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L434-440)
```rust
            match data {
                // No need to check sender for chunk
                DataOnDa::Chunk(chunk) => {
                    log!("Found chunk");

                    ChunkAccessor::<S>::insert(blob.wtxid(), chunk, &mut working_set);
                }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L467-502)
```rust
                DataOnDa::Aggregate(_, wtxids) => {
                    log!("Found aggregate proof");
                    if blob.sender().as_ref() != batch_prover_da_public_key {
                        log!(
                            "Aggregate proof sender is not batch prover, wtxid={:?}",
                            blob.wtxid()
                        );
                        continue;
                    }

                    let mut complete_proof = Vec::new();

                    // Ensure that aggregate has all the needed chunks.
                    for wtxid in &wtxids {
                        match ChunkAccessor::<S>::get(*wtxid, &mut working_set) {
                            Some(chunk) => {
                                if chunk.len() + complete_proof.len() > MAX_COMPRESSED_BLOB_SIZE {
                                    log!(
                                        "Compressed aggregate too large, wtxid={:?}; skipping",
                                        blob.wtxid()
                                    );
                                    continue 'blob_loop;
                                }

                                complete_proof.extend_from_slice(&chunk);
                            }
                            None => {
                                log!(
                                    "Unknown chunk in aggregate proof, parent={:?}, child={:?}; skipping",
                                    blob.wtxid(),
                                    wtxid
                                );
                                continue 'blob_loop;
                            }
                        }
                    }
```

**File:** crates/bitcoin-da/src/helpers/builders/body_builders.rs (L524-528)
```rust
    // push body in chunks of 520 bytes
    for chunk in reveal_body.chunks(520) {
        reveal_script_builder = reveal_script_builder
            .push_slice(PushBytesBuf::try_from(chunk.to_vec()).expect("Cannot push body chunk"));
    }
```
