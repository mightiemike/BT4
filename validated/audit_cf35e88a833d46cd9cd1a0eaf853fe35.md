### Title
Missing per-iteration sequencer-commitment index sequentiality check in `apply_l2_blocks_from_sequencer_commitments` allows `sequencer_commitment_index_range` to misrepresent a non-consecutive commitment set - ([File: crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs])

### Summary
`StfBlueprint::apply_l2_blocks_from_sequencer_commitments` only asserts L2-height sequentiality (`last_commitment_end_height + 1 == sequencer_commitment_l2_start_height`) inside its main `for` loop over `sequencer_commitments`, but never asserts `sequencer_commitments[i].index == sequencer_commitments[i-1].index + 1` for `i >= 1`. It only checks the index linkage at the two boundary points: `previous_sequencer_commitment.index + 1 == sequencer_commitments[0].index` (or `sequencer_commitments[0].index == 1` when there is no previous commitment). This lets `sequencer_commitment_index_range = (first.index, last.index)` claim a gapless index run for commitments that were not actually consecutive by index.

### Finding Description
The binding under test: `sequencer_commitment_index_range.1 - sequencer_commitment_index_range.0 + 1 == sequencer_commitment_hashes.len()`, which should equal the count of genuinely consecutive sequencer-signed indices consumed.

In the code: [1](#0-0) 
the range is computed purely from `.first()` and `.last()` of the input vector, before any per-element validation happens.

Inside the loop, only L2 height contiguity is enforced: [2](#0-1) 
There is no `assert_eq!(sequencer_commitment.index, previous_index + 1)` anywhere in this loop. The only index checks that exist are the boundary checks against `previous_sequencer_commitment`/first element: [3](#0-2) 

Because `SequencerCommitment.index` is not re-validated against `l2_end_block_number` continuity for every element in the middle of the vector, a `Vec<SequencerCommitment>` whose `.index` fields contain a gap (e.g. `1, 2, 4`) but whose `l2_end_block_number` values remain strictly sequential would pass every assertion in this function, while `sequencer_commitment_index_range` would report `(1, 4)` even though only 3 hashes are present in `sequencer_commitment_hashes` (`sequencer_commitment_hashes.len() == 3` vs. claimed range width `4`).

This output (`sequencer_commitment_index_range`, `sequencer_commitment_hashes`) is exactly what is fed forward into `BatchProofCircuitOutputV3` in `citrea_stf::verifier::StateTransitionVerifier::run_sequencer_commitments_in_da_slot`: [4](#0-3) 
and is subsequently consumed by `LightClientProofCircuit::verify_batch_proof_seq_comm_relation`, which relies on this range to reconcile batch-proof outputs against its own tracked "next expected commitment index" state.

### Impact Explanation
If this mismatch is not independently re-validated inside `verify_batch_proof_seq_comm_relation` (which I could not fully verify from the excerpted code in this session), the light client would advance its tracked commitment-index bookkeeping and `last_l2_state_root` based on a claimed range that does not correspond to the actual contiguous set of signed commitment indices consumed. This would fall into the Critical category: "a batch or light client proof accepted for a state transition that did not happen," and could cause honest light clients to converge on an incorrect index/state mapping. The blast radius would be systemic to every node/proof tracking commitment indices in sequence, not limited to a single block.

### Likelihood Explanation
The precondition for actual exploitation is that the attacker's own batch prover can supply `SequencerCommitment` entries with a genuine index/height mismatch as private guest input. This requires either (a) a batch producer/DA scenario where the sequencer itself posts index-skipped-but-height-contiguous commitments (not attacker controlled), or (b) the absence of any independent DA-inclusion/signature binding of the `.index` field to the commitment bytes elsewhere in the pipeline outside this function — a fact I was not able to conclusively confirm within the scope of the files reviewed in this session. Without confirming that no other component (DA inclusion proof, commitment signature check, or `verify_batch_proof_seq_comm_relation`'s own index continuity check) closes this gap, I cannot assert this is fully exploitable end-to-end by an unprivileged attacker as described.

### Recommendation
Add an explicit per-iteration assertion inside the `for sequencer_commitment in sequencer_commitments.into_iter()` loop binding index continuity, e.g. tracking `previous_index` and asserting `sequencer_commitment.index == previous_index + 1` on every iteration (mirroring the existing L2-height sequentiality assert at [5](#0-4) ), and separately assert that `sequencer_commitment_index_range.1 - sequencer_commitment_index_range.0 + 1 == sequencer_commitment_hashes.len()` before returning `ApplySequencerCommitmentsOutput`.

### Proof of Concept
`cargo test` in `crates/citrea-stf/tests/blueprint.rs`:
1. Construct `sequencer_commitments: Vec<SequencerCommitment>` with `.index` = `[1, 2, 4]` but `.l2_end_block_number` sequential/contiguous (e.g., covering L2 heights 1-10, 11-20, 21-30) and valid merkle roots matching a `MockZkGuest`-fed L2 block sequence.
2. Call `StfBlueprint::apply_l2_blocks_from_sequencer_commitments` (via `StateTransitionVerifier::run_sequencer_commitments_in_da_slot`) with a `MockZkGuest`.
3. Assert the call does not panic.
4. Assert `sequencer_commitment_index_range == (1, 4)` while `sequencer_commitment_hashes.len() == 3`, demonstrating `sequencer_commitment_index_range.1 - sequencer_commitment_index_range.0 + 1 (== 4) != sequencer_commitment_hashes.len() (== 3)`.

**Caveat**: I could not fully verify, within this session's tool budget, whether `verify_batch_proof_seq_comm_relation` in `crates/light-client-prover/src/circuit/mod.rs` or another upstream DA-inclusion check independently re-derives/validates the index continuity against raw DA blob data, which would neutralize this gap at a higher layer. This should be checked before treating the finding as fully confirmed end-to-end exploitable.

### Citations

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L487-490)
```rust
        let sequencer_commitment_index_range = (
            sequencer_commitments.first().unwrap().index,
            sequencer_commitments.last().unwrap().index,
        );
```

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L562-592)
```rust
        let (previous_commitment_index, previous_commitment_hash) =
            if let Some(previous_sequencer_commitment) = previous_sequencer_commitment {
                // The only way there would be a 0 indexed commitment is if the previous commitment somehow has index 0
                // This assertion will block that
                assert!(
                    previous_sequencer_commitment.index != 0,
                    "Previous sequencer commitment index must be non-zero"
                );

                // The index of the previous commitment should be one less than the first commitment
                assert_eq!(
                    previous_sequencer_commitment.index + 1,
                    sequencer_commitments[0].index,
                    "Sequencer commitments must be sequential"
                );
                // If there exists a previous commitment, then the first l2 block to prove
                // should be the one after the last commitment
                previous_batch_proof_l2_end_height =
                    previous_sequencer_commitment.l2_end_block_number;
                (
                    Some(previous_sequencer_commitment.index),
                    Some(previous_sequencer_commitment.serialize_and_calculate_sha_256()),
                )
            } else {
                // If this is the first batch proof, then the first commitment idx should be 1
                assert_eq!(
                    sequencer_commitments[0].index, 1,
                    "First commitment must be index 1"
                );
                (None, None)
            };
```

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L608-617)
```rust
        for sequencer_commitment in sequencer_commitments.into_iter() {
            // if the commitment is not sequential, then the proof is invalid.

            assert_eq!(
                last_commitment_end_height + 1,
                sequencer_commitment_l2_start_height,
                "Sequencer commitments must be sequential"
            );

            last_commitment_end_height = sequencer_commitment.l2_end_block_number;
```

**File:** crates/citrea-stf/src/verifier.rs (L65-116)
```rust
        let ApplySequencerCommitmentsOutput {
            state_roots,
            state_diff,
            last_l2_height,
            final_l2_block_hash,
            sequencer_commitment_hashes,
            sequencer_commitment_index_range,
            previous_commitment_index,
            previous_commitment_hash,
            cumulative_state_log,
        } = self.app.apply_l2_blocks_from_sequencer_commitments(
            guest,
            sequencer_public_key,
            initial_prev_l2_block_hash,
            &data.initial_state_root,
            pre_state.clone(),
            data.previous_sequencer_commitment,
            data.prev_hash_proof,
            data.sequencer_commitments,
            &data.cache_prune_l2_heights,
            forks,
        );

        println!("out of apply_l2_blocks_from_sequencer_commitments");

        let last_queried_hash = SHORT_HEADER_PROOF_PROVIDER
            .get()
            .unwrap()
            .take_last_queried_hash();

        let last_l1_hash = if let Some(hash) = last_queried_hash {
            hash
        } else {
            get_last_l1_hash_on_contract::<ZkDefaultContext>(
                cumulative_state_log,
                pre_state,
                &mut data.last_l1_hash_witness,
                *state_roots.last().unwrap(),
            )
        };

        BatchProofCircuitOutput::V3(BatchProofCircuitOutputV3 {
            state_roots,
            final_l2_block_hash,
            state_diff,
            last_l2_height,
            sequencer_commitment_hashes,
            last_l1_hash_on_bitcoin_light_client_contract: last_l1_hash,
            sequencer_commitment_index_range,
            previous_commitment_index,
            previous_commitment_hash,
        })
```
