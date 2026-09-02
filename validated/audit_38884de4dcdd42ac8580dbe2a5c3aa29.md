## Title
Batch-proof method-id activation is ordered by Bitcoin in-block wtxid position, not by activation height, allowing a lower-height authorized upgrade to be permanently dropped if mined after a higher-height upgrade in the same block - ([File: crates/light-client-prover/src/circuit/mod.rs])

## Summary
`LightClientProofCircuit::run_l1_block` processes `DataOnDa::BatchProofMethodId` blobs strictly in the order they appear in `da_txs`, which itself preserves the exact in-block wtxid order returned by `BitcoinVerifier::verify_transactions` (order of `inclusion_proof.wtxids`, whose correctness is only checked against the block's merkle root, not against activation height). The activation check `batch_proof_method_id.body.activation_l2_height <= last_activation_height` compares only against the highest height inserted so far in that pass, so if a higher-activation-height (H2) council transaction is mined before a lower-activation-height (H1) council transaction within the same block, H1 is permanently rejected even though it is validly 3-of-5 signed.

## Finding Description
The binding claimed to hold is: for every valid, 3-of-5 council-signed `BatchProofMethodId` body with `activation_l2_height = H`, `BatchProofMethodIdAccessor` state after light-client-proof processing contains an entry for H. This binding breaks when two valid signed method-id bodies for H1 < H2 are both included in the same Bitcoin block, with H2's wtxid preceding H1's wtxid in `inclusion_proof.wtxids`.

Code path: [1](#0-0)  shows `verify_transactions` builds `blobs` in exactly the order of `inclusion_proof.wtxids` (filtered by prefix, zipped with the completeness proof), with the only structural checks being that each `wtxid` matches its `calculate_wtxid(tx)` and that the overall merkle root of `inclusion_proof.wtxids` matches `block_header.txs_commitment`/`merkle_root()` — i.e., the check enforces that the claimed order is the *real* mined order, but does not enforce that DataOnDa::BatchProofMethodId transactions be ordered by `activation_l2_height`.

`run_l1_block` then iterates this vector in the received order: [2](#0-1) 

Since `last_activation_height` is recomputed from `BatchProofMethodIdAccessor::get(...).last().0` after each insert, once H2 is inserted first, H1's later check `H1 <= H2` is true, causing `continue` (skip) rather than error, silently and permanently dropping H1's insert — there's no retry or reconciliation phase, and the light-client proof output/state is committed forward, so this cannot be corrected by a later block (a resubmission of the same H1 body would still fail the same `<=` check forever since `last_activation_height` only grows).

Root cause: within-block transaction order on Bitcoin is a pure miner choice (not a consensus-enforced ordering by any application-level field), and the circuit conflates "position in block" with "chronological signing/activation order," despite both transactions being fully valid, correctly and independently 3-of-5 signed by the security council for their own bodies (`verify_method_id_security_council` at [3](#0-2)  validates each body independently and has no cross-transaction ordering check).

No existing guard prevents this: `verify_transactions` only proves inclusion/order-matches-block, not activation-height ordering; `run_l1_block`'s `<=` check is precisely the mechanism that causes the loss, not a defense against it.

## Impact Explanation
The immediate effect is that a genuinely council-authorized method-id upgrade for height H1 becomes permanently unappliable/unprovable in the light client's JMT state — it can never be inserted because `last_activation_height` (now H2) only increases. This has second-order Critical consequences for batch proof verification: `process_complete_proof`'s method-id lookup uses `binary_search_by_key` over the currently stored `(height, method_id)` list at [4](#0-3) ; with H1 missing, any batch proof whose `last_l2_height` falls in `[H1, H2)` will be verified against the wrong (older, pre-H1) method id rather than the one the council intended for that range, meaning the light client could accept/reject batch proofs using an unintended circuit version for that L2 range. This is a genuine, repeatable state divergence from the "true" authorized upgrade schedule and matches the Critical category "a true [upgrade/proof] made unprovable" — however, it is scoped to the light-client-prover module rather than `crates/bitcoin-da/src/lib.rs` as the question states (that file/scope does not exist as described; the real logic lives in `crates/light-client-prover/src/circuit/mod.rs`).

## Likelihood Explanation
This requires the operationally unusual precondition that the security council broadcasts two upgrade transactions for different activation heights before either confirms, and that both land in the same Bitcoin block with reversed relative order. Since Bitcoin miners are free to order independent transactions within a block however they choose (there is no protocol rule tying order to fee-rate for unrelated transactions), an attacker who can influence miner selection (via relay-only broadcast timing or bribing/relaying preferentially, as granted in the precondition) can plausibly cause this reversed ordering, though it depends on the council's own operational practice of issuing sequential upgrades close in time, which is outside attacker control and likely rare.

## Recommendation
Sort/process `BatchProofMethodId` transactions within a block (or across the whole `run_l1_block` pass) by `activation_l2_height` ascending before applying the `<=`/insert logic, rather than relying on `inclusion_proof.wtxids` order; alternatively, buffer all such blobs seen in the block, sort by `activation_l2_height`, and apply them in that sorted order after verifying signatures.

## Proof of Concept
`cargo test` (in `crates/light-client-prover/src/tests` area, using the existing test harness in `bin/citrea/tests/bitcoin/light_client_test.rs`):
1. Construct two valid `BatchProofMethodId` bodies with `activation_l2_height = H1` and `H2` (`H1 < H2`), each independently signed with 3-of-5 valid council signatures using `create_valid_signatures`/`generate_initial_pub_keys_with_signers` helpers.
2. Build two `BlobWithSender`/`DS::BlobTransaction` equivalents (or directly construct `inclusion_proof.wtxids`/`completeness_proof` fixtures) such that the wtxid ordering places H2's transaction before H1's transaction within the same simulated block.
3. Call `LightClientProofCircuit::run_l1_block` with both blobs in that order.
4. Assert `BatchProofMethodIdAccessor::get(&mut working_set)` contains an entry with height `H1` — expect the assertion to FAIL (entry absent), demonstrating H1 is permanently dropped, versus the entry existing if presented in ascending height order.

### Citations

**File:** crates/bitcoin-da/src/verifier.rs (L93-181)
```rust
    fn verify_transactions(
        &self,
        block_header: &<Self::Spec as DaSpec>::BlockHeader,
        inclusion_proof: <Self::Spec as DaSpec>::InclusionMultiProof,
        completeness_proof: <Self::Spec as DaSpec>::CompletenessProof,
    ) -> Result<Vec<<Self::Spec as DaSpec>::BlobTransaction>, Self::Error> {
        if block_header.tx_count as usize != inclusion_proof.wtxids.len() {
            return Err(ValidationError::HeaderInclusionTxCountMismatch);
        }

        let prefix = self.reveal_tx_prefix.as_slice();

        // Optimistically assume all txs in the completeness proof are verifiable
        let mut blobs = Vec::with_capacity(completeness_proof.len());

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
                    ParsedTransaction::Complete(complete) => {
                        if let Some(hash) = complete.get_sig_verified_hash() {
                            // complete.body is compressed, but we'll leave the compression to
                            // circuit logic

                            blobs.push(BlobWithSender::new(
                                complete.body,
                                complete.public_key,
                                hash,
                                *wtxid,
                            ))
                        }
                    }
                    ParsedTransaction::Aggregate(aggregate) => {
                        if let Some(hash) = aggregate.get_sig_verified_hash() {
                            blobs.push(BlobWithSender::new(
                                aggregate.body,
                                aggregate.public_key,
                                hash,
                                *wtxid,
                            ))
                        }
                    }
                    ParsedTransaction::Chunk(chunk) => {
                        blobs.push(BlobWithSender::new(
                            chunk.body,
                            // chunk sender and hash irrelevant
                            vec![],
                            [0; 32],
                            *wtxid,
                        ));
                    }
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
                    ParsedTransaction::SequencerCommitment(seq_comm) => {
                        if let Some(hash) = seq_comm.get_sig_verified_hash() {
                            blobs.push(BlobWithSender::new(
                                seq_comm.body,
                                seq_comm.public_key,
                                hash,
                                *wtxid,
                            ));
                        }
                    }
                }
            }
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
