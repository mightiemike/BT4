### Title
Council-authorised `BatchProofMethodId` upgrades can be permanently blocked by front-running an out-of-order but genuinely-signed future body - (File: `crates/light-client-prover/src/circuit/mod.rs`)

### Summary
`run_l1_block`'s `DataOnDa::BatchProofMethodId` branch enforces only a strictly-increasing `activation_l2_height` ordering and never checks `blob.sender()`, so any unprivileged party who observes a genuinely council-signed `BatchProofMethodIdBody` for a higher, later-intended activation height can inscribe it ahead of a lower, earlier-intended council body. This permanently blocks the earlier legitimate upgrade because the monotonicity check `activation_l2_height <= last_activation_height` never allows it to be inserted afterward.

### Finding Description
Binding claimed to hold: `activation_order_intended == activation_order_applied`, i.e. the sequence of `(activation_l2_height, method_id)` pairs the security council intends to activate, in the order it signs/publishes them, should equal the sequence actually recorded by `BatchProofMethodIdAccessor::insert`.

Code path:
- `BatchProofMethodIdAccessor::insert` just appends `(activation_l2_height, method_id)` to the stored vector with no ordering guarantee of its own [1](#0-0) .
- The only ordering enforcement lives in `run_l1_block`'s `DataOnDa::BatchProofMethodId` branch: it reads `last_activation_height = batch_proof_method_ids.last()....0` and rejects the new body only if `activation_l2_height <= last_activation_height`; otherwise, after `verify_method_id_security_council` passes, it calls `insert` [2](#0-1) .
- Unlike `DataOnDa::SequencerCommitment`, which checks `blob.sender().as_ref() != sequencer_da_public_key` [3](#0-2) , the `BatchProofMethodId` branch performs no such sender check. Authenticity is instead delegated entirely to `verify_method_id_security_council`, which validates the 3-of-5 signatures over `body.serialize()` against fixed constant pubkeys, independent of who submitted the DA transaction [4](#0-3) . This is confirmed by the test harness explicitly using an arbitrary sender key ("Method id sender private key, can be any sender") [5](#0-4) .
- `BatchProofMethodIdBody` carries no nonce, sequence number, or "previous activation height" binding — only `method_id`, `activation_l2_height`, `chain_id` — so nothing ties a signed body to a specific position in the activation sequence besides raw numeric comparison to whatever is currently `last()` in the accessor.

Exploit flow: the security council signs two valid bodies, one for a near-term fix at height `H1` and one for a further-out change at `H2` (`H1 < H2`). Because signing is off-chain (gathering 3-of-5 signatures takes time) and the two DA transactions can be broadcast to the Bitcoin mempool close together, an unprivileged attacker who observes the mempool (or any other public channel) can extract the fully-signed `BatchProofMethodId(H2)` blob bytes and, since no sender check exists, construct and pay to mine their own inscription carrying that identical blob ahead of (or in an earlier block than) the council's `H1` transaction. `run_l1_block` then processes `H2` first: `last_activation_height` becomes `H2`. When the council's genuinely-signed `H1` body is later processed, `H1 <= last_activation_height (H2)` is true, so it is silently skipped forever via `continue` — there is no way to ever insert it again.

Downstream effect: `process_complete_proof` selects the method id for a given batch proof purely by `binary_search_by_key` over `batch_proof_output_last_l2_height` against the stored `(height, method_id)` vector [6](#0-5) . If a batch prover produces a genuine proof for L2 heights in `[H1, H2)` using the circuit/method-id that was supposed to activate at `H1`, `Z::verify` will fail because the LCP still expects the pre-`H1` method id for that range — the true, council-authorised state transition becomes permanently unprovable/unverifiable by light clients for that range.

### Impact Explanation
A legitimate, council-authorised batch-proof method-id upgrade can be permanently dropped from the light client's JMT state while a later, out-of-sequence upgrade is applied instead — this corrupts the mapping used by `process_complete_proof` to select the correct circuit verifier key for every batch proof at L2 heights covered by the skipped upgrade. This matches the Critical category "a true [proof] made unprovable" / method-id upgrade accepted in the wrong order, since honest batch provers proving under the intended `H1` circuit would have their proofs permanently rejected by every honest light-client prover, and the effect is irreversible (the accessor never reorders or removes entries) and persists for all future blocks/provers built on that chain of light client proofs.

### Likelihood Explanation
Exploitation requires two independent council-signed `BatchProofMethodId` bodies for different future heights to exist and be observable (e.g. in the Bitcoin mempool) before the lower one is confirmed — a workflow the council could avoid entirely by submitting method-id upgrades strictly one at a time and waiting for finality before preparing/broadcasting the next. Given that operational constraint, likelihood is low-to-moderate: it depends on council operational practice rather than any protocol-level ordering guard, since the code itself provides no defense (no sender check, no nonce, no "next-expected" pointer) — only external process discipline currently prevents it. Attacker cost is a normal Bitcoin inscription fee; the attacker does not need to forge anything.

### Recommendation
Bind each `BatchProofMethodIdBody` to an explicit sequence/nonce (e.g. an incrementing index or the previous activation height/method id it supersedes) that must match the current on-chain `last()` entry, similar to how `verify_batch_proof_seq_comm_relation` binds each sequencer commitment to its predecessor. This makes a signed body valid only when applied immediately after the specific state it was authored against, preventing a later-authorised, higher-height body from being inserted out of turn ahead of an earlier one.

### Proof of Concept
In `crates/light-client-prover/src/tests/mod.rs` (or an integration test under `bin/citrea/tests/bitcoin/light_client_test.rs`), construct two valid `BatchProofMethodIdBody`/`BatchProofMethodId` values with real security-council signatures at heights `H1=210` and `H2=220` (as done for `LightClientBatchProofMethodIdUpdateSecurityCouncilTest`, reusing `create_valid_signatures`/`BATCH_PROOF_METHOD_ID_UPDATE_AUTHORITY_TEST_PRIVATE_KEYS`). Feed the `H2` blob before the `H1` blob in the same `da_txs`/`completeness_proof` vector passed to `run_l1_block`/`run_circuit`. Assert:
- `BatchProofMethodIdAccessor::<S>::get(...)` contains `(H2, method_id_2)` but never contains `(H1, method_id_1)`.
- A subsequent DA block re-submitting the `H1` body (still correctly signed) is processed and confirm it is skipped (`continue` path hit, accessor unchanged) — mirroring the existing pattern used in `crates/light-client-prover/src/tests/mod.rs:1146-1168` which already demonstrates the monotonic-height rejection, but reordered to show a legitimate earlier body being permanently dropped rather than a replay of an already-applied one.

### Citations

**File:** crates/light-client-prover/src/circuit/accessors.rs (L319-327)
```rust
    pub fn insert(activation_l2_height: u64, method_id: [u32; 8], working_set: &mut WorkingSet<S>) {
        let key = Self::key();
        let mut method_ids = Self::get(working_set).unwrap_or_default();
        method_ids.push((activation_l2_height, method_id));
        let value: StorageValue = borsh::to_vec(&method_ids)
            .expect("Batch proof method ids serialization should not fail")
            .into();
        working_set.set(&key, value);
    }
```

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

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-566)
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
                }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L567-575)
```rust
                DataOnDa::SequencerCommitment(commitment) => {
                    log!("Found sequencer commitment with index {}", commitment.index);
                    if blob.sender().as_ref() != sequencer_da_public_key {
                        log!(
                            "Sequencer commitment sender is not sequencer, wtxid={:?}",
                            blob.wtxid()
                        );
                        continue;
                    }
```

**File:** crates/light-client-prover/src/circuit/method_id_verifier.rs (L14-69)
```rust
pub fn verify_method_id_security_council(
    initial_da_pubkeys: [[u8; SECURITY_COUNCIL_COMPRESSED_PUBKEY_SIZE];
        SECURITY_COUNCIL_MEMBER_COUNT],
    msg: &[u8],
    signatures_with_idx: &[([u8; SECURITY_COUNCIL_SIGNATURE_SIZE], u8);
         SECURITY_COUNCIL_SIGNATURE_THRESHOLD],
) -> bool {
    // EIP-191 prefix + keccak256 → 32-byte prehash
    let prehash = eip191_hash_message(msg);

    // Check that signature indices are within bounds
    for &(_, index) in signatures_with_idx {
        if index >= 5 {
            log!("Invalid signature index: {}", index);
            return false;
        }
    }

    // Make sure the indexes are in ascending order to prevent duplicates
    for i in 0..signatures_with_idx.len() - 1 {
        if signatures_with_idx[i].1 >= signatures_with_idx[i + 1].1 {
            log!(
                "Signature indices are not in ascending order, failing indices: {}, {}",
                signatures_with_idx[i].1,
                signatures_with_idx[i + 1].1
            );
            return false;
        }
    }

    for signature_with_idx in signatures_with_idx.iter() {
        let signature = signature_with_idx.0;
        let pubkey_idx = signature_with_idx.1;
        let const_pubkey = initial_da_pubkeys[pubkey_idx as usize];

        // ensure the inscription pubkey matches the expected constant (compressed 33B)
        let verifying_key = VerifyingKey::from_sec1_bytes(const_pubkey.as_slice())
            .expect("Initial DA pubkeys must be parsable to k256 VerifyingKey form sec1 bytes");

        let Ok(parsed_sig) = Signature::from_bytes(&signature.into()) else {
            log!("Invalid signature format");
            return false; // invalid signature format, fail
        };

        // verify prehash with the matching verifying key
        if verifying_key
            .verify_prehash(prehash.as_slice(), &parsed_sig)
            .is_err()
        {
            log!("Signature verification failed for index: {}", pubkey_idx);
            return false;
        }
    }

    true
}
```

**File:** bin/citrea/tests/bitcoin/light_client_test.rs (L830-841)
```rust
        let bitcoin_da_service = spawn_bitcoin_da_service(
            &self.task_manager.executor(),
            &da.config,
            Self::test_config().dir,
            // Method id sender private key, can be any sender
            DaServiceKeyKind::Other(
                "79122E48DF1A002FB6584B2E94D0D50F95037416C82DAF280F21CD67D17D9077".to_string(),
            ),
            REVEAL_TX_PREFIX.to_vec(),
            None,
            None,
        )
```
