## Binding under test

`BatchProofMethodIdAccessor::get(ws).last().0` sequence of accepted `(activation_l2_height, method_id)` tuples **==** the sequence the security council actually signed and intended to have applied, in the order it intended (lower-height upgrade committed before a later, higher-height one it had also pre-signed).

## Trace

- `verify_method_id_security_council` signs/verifies only `BatchProofMethodIdBody { method_id, activation_l2_height, chain_id }` — no binding to a wtxid, txid, block height, nonce, or "previous accepted tuple": [1](#0-0) 
- In `run_l1_block`, the `DataOnDa::BatchProofMethodId` branch performs **no `blob.sender()` check** at all (unlike `Complete`/`Aggregate`/`SequencerCommitment`), because the comment explicitly states the sender field is unused — authority is carried entirely by the embedded signatures: [2](#0-1)  and confirmed by [3](#0-2) .
- The only ordering guard is `activation_l2_height <= last_activation_height → skip`, evaluated in `da_txs` iteration order: [4](#0-3) .
- `da_txs`/`completeness_proof` order is derived purely from the physical order of `wtxids` in the mined block (`BitcoinVerifier::verify_transactions`), not from any council-committed sequence: [5](#0-4) .
- `BatchProofMethodIdAccessor::insert` simply appends whatever is processed first: [6](#0-5) .

Because the signature covers only `(method_id, activation_l2_height, chain_id)` with no anti-replay/nonce/tx-binding, and the sender is never checked, **any unprivileged party who has observed an already-public, validly-signed `BatchProofMethodId` body/signature set (e.g., pending in mempool, or previously inscribed) can copy those exact bytes into a self-funded reveal transaction** and get it positioned earlier in a block than the council's own transaction for a lower, intended-to-be-first activation height. If that copy (height `Y`) lands first, `last_activation_height` jumps to `Y`; the legitimate lower-height (`X<Y`) blob processed afterward in the same block then fails `X <= last_activation_height` and is silently `continue`d — permanently dropped, since activation height must strictly increase.

Determinism does hold (`DETERMINISM`): all honest nodes/provers see the same block, iterate `da_txs` in the same order, and converge on the same resulting `BatchProofMethodIdAccessor` sequence and journal. But that converged sequence is not the one the council intended (`X` then `Y`) — it is `Y` only, with `X` unrecoverably skipped, because nothing in `verify_method_id_security_council` or `run_l1_block` binds acceptance order to the council's intended reveal order or to a specific DA transaction. This is a genuine `AUTHORITY` binding break independent of `DETERMINISM`.

### Title
Unauthorised council upgrade re-ordering via replayable `BatchProofMethodId` signatures with no reveal-transaction/nonce binding - (File: crates/light-client-prover/src/circuit/mod.rs)

### Summary
`BatchProofMethodIdBody` signatures cover only `(method_id, activation_l2_height, chain_id)` with no binding to a specific Bitcoin transaction, nonce, or council-intended sequence, and `run_l1_block` performs no sender check for this data type. Any unprivileged party who observes an already-public, validly-signed body for a higher activation height `Y` can copy it into their own funded transaction and get it processed ahead of the council's legitimate, lower-height (`X<Y`) update within the same block, causing `X` to be permanently dropped by the strict-monotonic gate.

### Finding Description
The equality `accepted (height, method_id) sequence == council-intended sequence` is broken. `verify_method_id_security_council` (crates/light-client-prover/src/circuit/method_id_verifier.rs:14-69) verifies signatures purely against the borsh-serialized `BatchProofMethodIdBody`, which contains no reference to any specific DA transaction, wtxid, block, or ordering nonce. In `run_l1_block`'s `DataOnDa::BatchProofMethodId` arm (crates/light-client-prover/src/circuit/mod.rs:529-566), acceptance requires only `activation_l2_height > last_activation_height` and a valid signature — `blob.sender()` is intentionally never checked (crates/bitcoin-da/src/verifier.rs:153-168), since the design assumes signatures alone carry authority.

Because the signed bytes are static and replayable, and `da_txs` are processed in the physical order transactions appear inside the Bitcoin block (`BitcoinVerifier::verify_transactions`, crates/bitcoin-da/src/verifier.rs:108-119), an attacker who has seen an already-public, validly-signed body for height `Y` (e.g. sitting unconfirmed in the Bitcoin mempool, or previously inscribed) can craft and fund their own reveal transaction embedding an identical copy of that body+signatures, and get it included at an earlier tx position than the council's own transaction for height `X<Y` within the same block. When `Y`'s copy is processed first, `last_activation_height` becomes `Y`; the legitimate `X` blob processed later in the same block then fails `X <= last_activation_height` and is dropped via `continue`, never entering `BatchProofMethodIdAccessor`.

Existing guards do not prevent this: the monotonic height check enforces internal self-consistency of the stored sequence but not that the sequence matches the order/content the council intended to commit; the sender check is deliberately absent for this variant; and no nonce/expiry/prior-tuple binding exists in the signed message.

### Impact Explanation
A batch-proof method-id upgrade that was never intended to activate first (or at all, out of order) becomes the accepted upgrade path, while the council's intended earlier upgrade is silently and permanently discarded — this is an unauthorised method-id upgrade ordering accepted by the light client circuit, matching the Critical category "a sequencer commitment or method-id upgrade accepted that was never authorised." All honest full nodes and honest light client provers converge on the same (wrong) sequence, so there is no fork, but the resulting proof verification set no longer reflects the council's authorized rollout plan, potentially skipping a required intermediate method id (e.g. a bugfix release) before a later one takes effect. This is repeatable any time the council has more than one pre-signed/pending update visible before final confirmation.

### Likelihood Explanation
Requires: (1) the council to have a body+signature set for a future/higher activation height that becomes observable before it is confirmed on L1 (e.g., broadcast to the Bitcoin mempool, or previously inscribed then reorganized/replaced), and (2) the attacker to pay Bitcoin fees to get their copy transaction mined at an earlier position than the legitimate lower-height transaction within the same block (or an earlier block). No privileged key or role is needed — only mempool observation and normal Bitcoin fee payment, both within the defined unprivileged attacker capabilities.

### Recommendation
Bind the signed `BatchProofMethodIdBody` message to an anti-replay/ordering element, e.g. include the previously-active method id/height (or a monotonically increasing council-assigned sequence number) inside the signed body so a given signature can only ever extend one specific point in the chain, or additionally require `blob.sender()`/tx-specific commitments so a valid council body cannot be replayed via an arbitrary attacker-funded transaction to jump the queue.

### Proof of Concept
`cargo test` in `crates/light-client-prover`:
1. Build two valid `BatchProofMethodId` blobs via `create_new_method_id_tx` (test_utils.rs): `blob_x` with `activation_l2_height = 100, method_id = [1;8]`, and `blob_y` with `activation_l2_height = 200, method_id = [2;8]`, both signed with the same test council keys.
2. Call `LightClientProofCircuit::run_l1_block` (or the native circuit runner) once with `completeness_proof = vec![blob_y.clone(), blob_x.clone()]` (adversary order, `Y` before `X`) for a mock L1 block.
3. Assert `BatchProofMethodIdAccessor::<ProverStorage>::get(&mut working_set).unwrap()` equals `[..., (200, [2;8])]` and does **not** contain `(100, [1;8])` — proving `X` was dropped.
4. Repeat with `completeness_proof = vec![blob_x.clone(), blob_y.clone()]` (intended order) and assert the accessor now contains both `(100,[1;8])` then `(200,[2;8])`.
5. The divergence between step 3 and step 5's accepted sequences, from identical signed inputs differing only in intra-block order, demonstrates the `AUTHORITY` binding break while `DETERMINISM` (same order → same honest-node result) still holds.

### Citations

**File:** crates/light-client-prover/src/circuit/method_id_verifier.rs (L14-22)
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

**File:** crates/bitcoin-da/src/verifier.rs (L108-119)
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
