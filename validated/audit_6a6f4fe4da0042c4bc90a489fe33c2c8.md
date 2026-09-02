### Title
Missing per-commitment `.index` sequencing check in `apply_l2_blocks_from_sequencer_commitments` allows duplicated/skipped commitment indices to be baked into a valid batch proof - (File: crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs)

### Summary
`apply_l2_blocks_from_sequencer_commitments` only validates the `.index` field of the *first* commitment in the batch (against the previous commitment or against `1`), and thereafter validates only L2 block-height continuity, never re-checking that `sequencer_commitments[i].index == sequencer_commitments[i-1].index + 1` for `i > 0`. This lets a permissionless prover feed a `sequencer_commitments` vector containing a duplicated or skipped index while heights stay sequential, so `sequencer_commitment_index_range = (first.index, last.index)` and `sequencer_commitment_hashes` can encode a state where the count of indices in the range does not equal the number of unique indices actually hashed.

### Finding Description
The claimed binding is: for every index `i` in `[sequencer_commitment_index_range.0, sequencer_commitment_index_range.1]`, there exists exactly one entry in `sequencer_commitment_hashes`, i.e. `(range.1 - range.0 + 1) == sequencer_commitment_hashes.len()` AND all indices in that span are distinct and present.

In [1](#0-0) , `sequencer_commitment_hashes` and `sequencer_commitment_index_range` are computed directly from the raw `sequencer_commitments` vector, before any per-commitment sequencing assertion runs.

The only `.index` validation performed is on the first element of the vector: either it must equal the previous commitment's index + 1, or it must equal `1` if there is no previous commitment, as seen at [2](#0-1) . Inside the main per-commitment loop that follows ( [3](#0-2) ), the only continuity checks are on L2 block heights - `last_commitment_end_height + 1 == sequencer_commitment_l2_start_height` (line 611-615) and `sequencer_commitment.l2_end_block_number == l2_height - 1` (line 704). The `.index` field of the 2nd, 3rd, ... Nth commitment in the vector is never read or asserted against its neighbor inside this loop.

Because L2 height sequencing and `.index` sequencing are independent fields, an attacker who controls the host-side circuit input for their own permissionless batch-prover process can submit a `sequencer_commitments` vector where indices are e.g. `[5, 5, 7]` (duplicating 5, skipping 6) while the underlying L2 block ranges for those three commitments are genuinely sequential and pass every height-based assertion. The resulting output would have `sequencer_commitment_index_range = (5, 7)` and `sequencer_commitment_hashes.len() == 3`, which numerically satisfies a naive `(last - first + 1) == hashes.len()` check performed downstream, even though index 5 is duplicated and index 6 is entirely missing - a "compensating duplication" that a simple range-length check cannot detect.

I was not able to fully read the body of `verify_batch_proof_seq_comm_relation` in [4](#0-3)  before running out of tool iterations, so I cannot confirm with certainty whether it performs exactly the naive length check the question describes, or whether it additionally deduplicates/verifies distinctness of indices (which would neutralize this gap). This is a genuine, confirmed gap in the guest-side STF blueprint code, but the full downstream consequence in the light-client circuit (double counting in `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`) is unverified due to incomplete access to that function's implementation.

### Impact Explanation
If the downstream light-client relation check only validates the numeric span size against `hashes.len()` (as the question hypothesizes), a prover can produce a cryptographically valid zk proof whose claimed sequencer-commitment coverage silently skips or double-counts a commitment index. This could let honest full nodes and the light client diverge on which commitments/state roots are considered proven, or double-count a state root for the accessor that tracks per-index verified state transitions - matching the Critical category "a batch or light client proof accepted for a state transition that did not happen." However, without confirming the exact logic of `verify_batch_proof_seq_comm_relation`, I cannot state definitively that the downstream check is fooled in practice.

### Likelihood Explanation
The attacker only needs to run their own permissionless batch-prover binary and control the raw circuit inputs fed to the zkvm guest (no sequencer, DA, or council privileges required, consistent with the stated threat model). Constructing sequential L2 heights while manipulating the unrelated `.index` labels is mechanically straightforward given the confirmed absence of a per-element index check in the loop.

### Recommendation
Add an explicit assertion inside the per-commitment loop in `apply_l2_blocks_from_sequencer_commitments` that `sequencer_commitment.index == previous_index_in_this_batch + 1` for every commitment after the first, in addition to the existing height-based checks. Additionally, harden the downstream `verify_batch_proof_seq_comm_relation` to check strict index distinctness/completeness rather than only comparing the numeric span size to `hashes.len()`.

### Proof of Concept
Not fully producible without confirming `verify_batch_proof_seq_comm_relation`'s exact implementation. A cargo test in `crates/light-client-prover/src/tests/mod.rs` should: (1) build a mock batch-proof output with `sequencer_commitments` indices `[5, 5, 7]` and sequential-but-arbitrary L2 heights via `apply_l2_blocks_from_sequencer_commitments`-equivalent mock input, (2) assert this succeeds in the STF blueprint (confirming the gap), then (3) feed the resulting `sequencer_commitment_index_range=(5,7)` / `sequencer_commitment_hashes` (len 3) into `verify_batch_proof_seq_comm_relation` and assert it is rejected - if it is accepted, the vulnerability is confirmed end-to-end.

### Citations

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L482-490)
```rust
        let sequencer_commitment_hashes = sequencer_commitments
            .iter()
            .map(|c| c.serialize_and_calculate_sha_256())
            .collect::<Vec<_>>();

        let sequencer_commitment_index_range = (
            sequencer_commitments.first().unwrap().index,
            sequencer_commitments.last().unwrap().index,
        );
```

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L566-592)
```rust
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

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L608-709)
```rust
        for sequencer_commitment in sequencer_commitments.into_iter() {
            // if the commitment is not sequential, then the proof is invalid.

            assert_eq!(
                last_commitment_end_height + 1,
                sequencer_commitment_l2_start_height,
                "Sequencer commitments must be sequential"
            );

            last_commitment_end_height = sequencer_commitment.l2_end_block_number;

            // we must verify given DA headers match the commitments

            let mut l2_height = sequencer_commitment_l2_start_height;

            let state_change_count: u32 = guest.read_from_host();
            let mut l2_block_hashes = Vec::with_capacity(state_change_count as usize);

            for _ in 0..state_change_count {
                // there used to be a need for height to be passed before L2 block
                // now this is not needed but deployed provers still have the same input generation in place
                // so don't use this variable
                let _l2_block_l2_height = guest.read_from_host::<u64>();

                let (l2_block, state_witness, offchain_witness) =
                    guest.read_from_host::<(L2Block, Witness, Witness)>();

                assert_eq!(
                    l2_block.height(),
                    l2_height,
                    "L2 block height is not equal to the expected height"
                );

                if let Some(prev_hash) = prev_l2_block_hash {
                    assert_eq!(
                        l2_block.prev_hash(),
                        prev_hash,
                        "L2 block previous hash must match the hash of the block before"
                    );
                }

                fork_manager.register_block(l2_height).unwrap();

                let result = self
                    .apply_l2_block(
                        fork_manager.active_fork().spec_id,
                        &sequencer_public_key,
                        &current_state_root,
                        pre_state.clone(),
                        cumulative_state_log,
                        cumulative_offchain_log,
                        state_witness,
                        offchain_witness,
                        &l2_block,
                    )
                    // TODO: this can be just ignoring the failing seq. com.
                    // We can count a failed l2 block as a valid state transition.
                    // for now we don't allow "broken" seq. com.s
                    .expect("L2 block must succeed");

                assert_eq!(current_state_root, result.state_root_transition.init_root);
                current_state_root = result.state_root_transition.final_root;
                state_diff.extend(result.state_diff);

                // The state root of prover should match l2 block coming from sequencer
                assert_eq!(current_state_root, l2_block.state_root());

                let mut state_log = result.state_log;
                let mut offchain_log = result.offchain_log;
                // prune cache logs if it is hinted from native
                if cache_prune_l2_heights_iter
                    .next_if_eq(&&l2_height)
                    .is_some()
                {
                    state_log.prune_half();
                    offchain_log.prune_half();
                }

                l2_height += 1;
                prev_l2_block_hash = Some(l2_block.hash());
                l2_block_hashes.push(l2_block.hash());

                cumulative_state_log = Some(state_log);
                cumulative_offchain_log = Some(offchain_log);
            }

            // now verify the claimed merkle root of l2 block hashes
            let calculated_root =
                MerkleTree::<Sha256>::from_leaves(l2_block_hashes.as_slice()).root();

            assert_eq!(
                calculated_root,
                Some(sequencer_commitment.merkle_root),
                "Invalid merkle root"
            );

            assert_eq!(sequencer_commitment.l2_end_block_number, l2_height - 1);
            // Update next sequencer commitment start height
            sequencer_commitment_l2_start_height = l2_height;

            state_roots.push(current_state_root);
        }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L1-1)
```rust
//! # Light Client Circuit Module
```
