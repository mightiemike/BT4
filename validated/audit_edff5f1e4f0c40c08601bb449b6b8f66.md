### Title
Out-of-order `BatchProofMethodId` reveal permanently drops a lower, genuinely-authorised activation height - (File: `crates/light-client-prover/src/circuit/mod.rs`)

### Summary
`run_l1_block`'s handling of `DataOnDa::BatchProofMethodId` derives `last_activation_height` from `BatchProofMethodIdAccessor::get(...).last()` and rejects (`continue`) any incoming update whose `activation_l2_height` is `<=` that value. Because blobs are processed in raw in-block order and `BatchProofMethodId` reveals are not sender-restricted, an unprivileged party can copy a legitimately council-signed, higher-height message and get it processed before a legitimately council-signed, lower-height message in the same or an earlier block, permanently blocking the lower height from ever being inserted.

Note: the question cites `crates/sequencer/src/tx_validator.rs`, but no such logic exists there; the actual code lives in `crates/light-client-prover/src/circuit/mod.rs` (`LightClientProofCircuit::run_l1_block`).

### Finding Description
The binding claimed by the question is:
`∀ (h, m)` genuinely 3-of-5 council-signed and chain-id-matched ⇒ `(h, m) ∈ BatchProofMethodIdAccessor::get(...)`.

The code breaks this binding: [1](#0-0) 

```
DataOnDa::BatchProofMethodId(batch_proof_method_id) => {
    let batch_proof_method_ids = BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();
    let last_activation_height = batch_proof_method_ids.last().expect("Should be at least one").0;
    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
        continue;   // permanently dropped, no retry mechanism
    }
    ...
    BatchProofMethodIdAccessor::<S>::insert(...);
}
```

`last()` is simply the most recently *inserted* entry, not the maximum by height, and insertion order tracks the raw iteration order of `da_txs`, which itself preserves Bitcoin in-block transaction order (confirmed by `extract_relevant_blobs_with_proof` / `verify_transactions`, which push blobs in the order they appear in `block.txdata`) [2](#0-1) .

Critically, unlike `Complete`, `Aggregate`, and `SequencerCommitment` blobs, the `BatchProofMethodId` branch has **no `blob.sender()` check** — this is explicit and intentional in the DA layer because the security-council public keys are embedded in the signed body itself, not tied to the inscribing wallet: [3](#0-2) 

This means anyone who obtains the raw `borsh(DataOnDa::BatchProofMethodId(BatchProofMethodId{ body, signatures_with_index }))` bytes — regardless of who produced them — can create their own inscription (paying their own fees, with no DA key requirement) that carries the identical valid signed payload. An attacker who observes two genuinely council-signed messages for heights `H1 < H2` (e.g. via mempool observation or any public channel before either is confirmed) can inscribe a copy of the `H2` message and have it mined before the original `H1` message (in the same block via fee-driven ordering, or trivially in an earlier block). Once the `H2` entry is inserted, `last_activation_height = H2`, and the legitimate `H1` message will always fail the `<=` check and be `continue`d — with no path to ever being re-applied since the check is monotonic and one-directional.

### Impact Explanation
When `H1`'s update is dropped, `process_complete_proof`'s method-id lookup via binary search over `BatchProofMethodIdAccessor` [4](#0-3)  will select the method id active *before* `H1` for any L2 batch proof whose `last_l2_height` falls in `[H1, H2)`, instead of the method id the council actually authorised for that range. If the batch prover (operating per the council's real schedule) produces genuine proofs using the circuit intended for `[H1,H2)`, the light client circuit will attempt `Z::verify` with the wrong (stale) method id and fail verification of an otherwise-true state transition — i.e., a true state transition becomes unprovable to the light client, and/or different light-client provers that happened to see the reveals in different relative order could diverge on which method id is "current," causing two honest provers to commit different outputs for the same L1 block. This matches the Critical categories "a true [proof] made unprovable" and "a light client proof split where two honest provers commit different outputs for the same L1 block." The effect is permanent (no self-healing) and repeats identically at every future council-issued upgrade that an attacker chooses to interfere with.

### Likelihood Explanation
Preconditions required: two genuinely council-signed `BatchProofMethodId` messages for different future heights must be observable (e.g., in the P2P mempool, or via any public disclosure) before both are confirmed/processed by the light client. Given that precondition, the attacker's actions are fully within an unprivileged actor's capability — no DA key, no council key, no elevated node role — only the ability to construct and broadcast a Bitcoin inscription carrying copied bytes and pay fees to influence relative confirmation order. This does not require majority hashrate; only ordinary fee-based prioritization or opportunistic timing to get a copy confirmed before the original. Likelihood is conditional on the security council's operational practice of not always awaiting on-chain confirmation of one upgrade before signing/distributing the next, which is plausible but not verified as the guaranteed operational procedure in this repository.

### Recommendation
Compute `last_activation_height` as the maximum height across all stored entries (`iter().map(|(h,_)| *h).max()`) rather than `.last()`, and/or store/insert entries sorted by `activation_l2_height`, rejecting only insertion if an entry for that exact height already exists — not merely if it's less than the most-recently-inserted one. Additionally, consider enforcing uniqueness/idempotency of `(activation_l2_height)` regardless of arrival order so any correctly signed message for a still-unseen height is accepted no matter what order copies of other valid messages arrive in.

### Proof of Concept
In `crates/light-client-prover/src/circuit/accessors.rs` or `crates/light-client-prover/src/tests/mod.rs`, using existing test helpers (`create_new_method_id_tx`, `create_valid_signatures`, `generate_initial_pub_keys_with_signers_from_pks`):

1. Build two valid `BatchProofMethodId` `MockBlob`s: `blob_h1` with `activation_l2_height = 100`, `blob_h2` with `activation_l2_height = 200`, both signed with 3-of-5 valid signatures and the correct `chain_id`.
2. Construct `da_txs = vec![blob_h2, blob_h1]` (H2 before H1, simulating one L1 block).
3. Call `LightClientProofCircuit::run_l1_block(...)` with these `da_txs`.
4. Assert on both sides of the binding:
   - `BatchProofMethodIdAccessor::<S>::get(&mut working_set)` contains an entry with height `200` (present — expected).
   - `BatchProofMethodIdAccessor::<S>::get(&mut working_set)` also contains an entry with height `100` (currently **fails** — `H1` is silently dropped via `continue`, demonstrating the broken binding).




Actually, per the ask-only-mode instructions this is an informational/audit response and no plan should be produced. Disregard the empty plan tag above.

### Citations

**File:** crates/light-client-prover/src/circuit/mod.rs (L288-303)
```rust
        let batch_proof_method_ids = BatchProofMethodIdAccessor::<S>::get(working_set)
            .expect("Batch proof method ids must exist");

        let batch_proof_method_id = if batch_proof_method_ids.len() == 1 {
            batch_proof_method_ids[0].1
        } else {
            let idx = match batch_proof_method_ids
                // Returns err and the index to be inserted, which is the index of the first element greater than the key
                // That is why we need to subtract 1 to get the last element smaller than the key
                .binary_search_by_key(&batch_proof_output_last_l2_height, |(height, _)| *height)
            {
                Ok(idx) => idx,
                Err(idx) => idx.saturating_sub(1),
            };
            batch_proof_method_ids[idx].1
        };
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-565)
```rust
                DataOnDa::BatchProofMethodId(batch_proof_method_id) => {
                    log!("Found batch proof method id");
                    let batch_proof_method_ids =
                        BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();

                    let last_activation_height = batch_proof_method_ids
                        .last()
                        .expect("Should be at least one")
                        .0;

                    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
                        log!("Batch proof method id activation height is not greater than the last one");
                        continue;
                    }

                    let circuit_chain_id = citrea_network_to_chain_id(network);
                    if circuit_chain_id != batch_proof_method_id.body.chain_id {
                        log!("Method ID upgrade transactions chain ID does not match circuit chain ID");
                        continue;
                    }

                    // Verify the signatures only if the activation height is greater than the last one
                    // This prevents replay attacks of old method IDs
                    if !verify_method_id_security_council(
                        *method_id_upgrade_authority_da_public_keys,
                        batch_proof_method_id.body.serialize().as_slice(),
                        batch_proof_method_id.signatures_with_index(),
                    ) {
                        log!("Method ID security council verification failed");
                        continue;
                    }

                    BatchProofMethodIdAccessor::<S>::insert(
                        batch_proof_method_id.body.activation_l2_height,
                        batch_proof_method_id.body.method_id,
                        &mut working_set,
                    );
```

**File:** crates/bitcoin-da/src/verifier.rs (L108-120)
```rust
        let relevant_wtxid_iter = inclusion_proof
            .wtxids
            .iter()
            .filter(|wtxid| wtxid.starts_with(prefix));
        for (wtxid, tx) in relevant_wtxid_iter.zip_eq(&completeness_proof) {
            // ensure completeness proof tx matches the inclusion tx
            if &calculate_wtxid(tx) != wtxid {
                return Err(ValidationError::RelevantTxNotInProof);
            }

            // it must be parsed correctly
            if let Ok(parsed_tx) = parse_relevant_transaction(tx) {
                match parsed_tx {
```

**File:** crates/bitcoin-da/src/verifier.rs (L153-168)
```rust
                    // The verification of BatchProofMethodId security council signatures are done in the circuit
                    ParsedTransaction::BatchProofMethodId(method_id) => {
                        // Pubkey here is given as 0 because the security council pub keys are inside the body
                        let public_key = [0u8; 32].to_vec();
                        let hash = method_id.hash();

                        blobs.push(BlobWithSender::new(
                            // Body here is: borsh(DataOnDa::BatchProofMethodId(BatchProofMethodId { ... }))
                            // The sender field here is not used because this transaction has a security council
                            // consisting of 5 public keys, this data and signatures are embedded in the body
                            method_id.body,
                            public_key,
                            hash,
                            *wtxid,
                        ))
                    }
```
