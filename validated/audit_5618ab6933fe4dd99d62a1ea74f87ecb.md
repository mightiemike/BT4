### Title
Permissionless DA-arrival reordering of council-signed `BatchProofMethodId` upgrades permanently drops the earlier-activation upgrade - ([File: crates/light-client-prover/src/circuit/mod.rs])

### Summary
The `DataOnDa::BatchProofMethodId` branch in `run_l1_block` has no `blob.sender()` restriction (unlike `Complete`/`Aggregate` proofs and `SequencerCommitment`), so any unprivileged party can relay a validly council-signed `BatchProofMethodId` inscription. Combined with `BatchProofMethodIdAccessor::insert`'s monotonicity check that only compares against the *last inserted* entry rather than the council's intended chronological schedule, an attacker who controls DA inclusion order of two independently valid council-signed bodies can permanently and unrecoverably drop the one with the lower `activation_l2_height`.

### Finding Description
The claimed binding is: `BatchProofMethodIdAccessor::get(...).last().0` (the accessor's "current activation height ceiling") should equal "the highest `activation_l2_height` the security council has signed and intends to currently be active", not "whichever signed body an unprivileged relayer chose to get mined first."

Code path in `crates/light-client-prover/src/circuit/mod.rs`:
```
DataOnDa::BatchProofMethodId(batch_proof_method_id) => {
    let batch_proof_method_ids = BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();
    let last_activation_height = batch_proof_method_ids.last().expect("Should be at least one").0;
    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
        continue;
    }
    ...
    BatchProofMethodIdAccessor::<S>::insert(...);
}
``` [1](#0-0) 

Two properties make this exploitable by an unprivileged attacker:

1. No sender/authorization check gates who can submit a `BatchProofMethodId` blob — unlike the `Complete`/`Aggregate` proof branches (`blob.sender().as_ref() != batch_prover_da_public_key`) and `SequencerCommitment` (`blob.sender().as_ref() != sequencer_da_public_key`), the `BatchProofMethodId` branch relies solely on the embedded council signatures, so anyone who has the signed bytes (e.g. observed in mempool, or a not-yet-confirmed inscription) can copy and re-inscribe them, choosing fee/timing to control DA arrival order. [2](#0-1) [3](#0-2) 

2. `BatchProofMethodIdAccessor::insert` unconditionally appends `(activation_l2_height, method_id)` to a `Vec`, and its ordering guard only ever compares against the vector's current `last()`, not against any canonical council-issued sequence number or nonce: [4](#0-3) 

If council-signed body A (`activation_l2_height=100`) and body B (`activation_l2_height=200`) both exist as valid signed bytes, and B is DA-included in an earlier L1 block than A (attacker pays higher fees to confirm B's copy first), then when B is processed `last_activation_height` becomes 200. When A is later processed, `100 <= 200` is true, so A is skipped via `continue` and never appended — irrecoverably, since any future insertion also requires `activation_l2_height > 200`, and A's council-signed body is fixed at 100 and cannot be re-signed to a higher height without new council action.

Because `process_complete_proof` selects the effective method id via `binary_search_by_key` over this vector keyed on `batch_proof_output_last_l2_height` [5](#0-4) , any legitimate batch proof produced by the honest batch prover for L2 heights that were supposed to fall under body A's method id (e.g., heights in `[100, 200)`) will be verified by the light-client circuit against the wrong (pre-A, stale) method id, causing `Z::verify` to fail for a state transition that genuinely occurred and was properly authorized.

Existing guards do not prevent this: `verify_method_id_security_council` only checks the 3-of-5 signature threshold and index ordering, not against a canonical sequence/height ordering enforced independently of DA arrival; the chain-id check and activation-height check are the only ordering safeguards and both operate on DA arrival order, which the attacker fully controls.

### Impact Explanation
A properly council-authorized upgrade becomes permanently unappliable for its intended L2 height range, and any true batch-proof state transition relying on that method id for `[100, 200)` becomes permanently unprovable to the light client — this matches the explicitly listed Critical category "a true [state transition] made unprovable." The blast radius covers every full node and light-client prover that ever needs to verify batch proofs spanning that height range; the drop is permanent (the vector is append-only and strictly increasing), so no honest retry can recover it without a fresh council re-signature targeting an activation height beyond the attacker-inserted one, which cannot retroactively cover already-passed L2 heights.

### Likelihood Explanation
Exploitation requires the attacker to possess two independently valid, council-signed `BatchProofMethodId` bodies before/while they are competing for DA inclusion (e.g., observing one in the Bitcoin mempool and inscribing a copy of a different, higher-activation one with higher fees to confirm first). This is a realistic Bitcoin fee-market manipulation fully within the unprivileged attacker's capabilities (pay fees, inscribe/mine any transaction), and does not require key compromise — only that legitimate signed council update bytes become observable before being confirmed. The lack of a `blob.sender()` restriction on this branch (present on all sibling branches) is what makes third-party relaying and reordering possible at all.

### Recommendation
Add a `blob.sender()` restriction on `DataOnDa::BatchProofMethodId` consistent with `Complete`/`Aggregate`/`SequencerCommitment` branches, and/or bind the accessor's ordering check to a monotonic sequence number or nonce embedded and signed inside `BatchProofMethodIdBody` itself (rather than relying purely on DA arrival order via `last()`), so DA inclusion order cannot override the council's intended activation schedule.

### Proof of Concept
```rust
// crates/light-client-prover/src/circuit/accessors.rs (new test)
#[test]
fn test_reordered_valid_council_updates_drops_earlier_activation() {
    // 1. Council signs body_a { activation_l2_height: 100, method_id: [1;8] }
    // 2. Council signs body_b { activation_l2_height: 200, method_id: [2;8] }
    // 3. Simulate run_l1_block processing blob(body_b) in block N, then blob(body_a) in block N+1
    //    (attacker-controlled DA inclusion order)
    // 4. Assert BatchProofMethodIdAccessor::get() == vec![(0, initial), (200, [2;8])]
    //    i.e. (100, [1;8]) is permanently absent, even though council-signed and valid.
    // 5. Assert that for a batch proof whose last_l2_height=150, process_complete_proof
    //    selects the OLD/initial method id via binary_search, not [1;8],
    //    causing Z::verify to fail for a true state transition that used [1;8].
}
```
The binding to assert on both sides: LHS = `BatchProofMethodIdAccessor::get(...).last().0` after processing; RHS = "highest `activation_l2_height` among all council-signed bodies" (200 and 100). Before the reorder they'd match if inserted in height order; after the attacker's reorder, LHS=200 while the council's full authorized set still includes 100, and the entry for 100 is unrecoverably absent from storage.

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

**File:** crates/light-client-prover/src/circuit/mod.rs (L441-449)
```rust
                DataOnDa::Complete(proof) => {
                    log!("Found complete proof");
                    if blob.sender().as_ref() != batch_prover_da_public_key {
                        log!(
                            "Complete proof sender is not batch prover, wtxid={:?}",
                            blob.wtxid()
                        );
                        continue;
                    }
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
