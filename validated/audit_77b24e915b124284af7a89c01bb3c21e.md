### Title
Batch proof method-id selection by `last_l2_height` allows a legitimately-signed but adversarially-timed `BatchProofMethodId` update to permanently break verification of an in-flight batch proof - (File: `crates/light-client-prover/src/circuit/mod.rs`)

### Summary
`LightClientProofCircuit::process_complete_proof` selects the method id used to verify a `BatchProofCircuitOutput` purely by binary-searching `BatchProofMethodIdAccessor` state for the proof's `last_l2_height` (the *end* of the covered range), with no check that the same method id was active across the *entire* range the proof covers, and no coordination with how the batch prover itself decides which method id/ELF to use when generating the proof (which is driven by `fork_from_block_number`/`SpecId`, a completely separate table from `BatchProofMethodIdAccessor`). If a genuinely 3-of-5-signed `BatchProofMethodId` DA transaction with `activation_l2_height` landing strictly inside an already-generated, in-flight batch proof's `[start, last_l2_height]` range gets processed by the light client before that proof's `Complete(Proof)` blob, the lookup silently switches to the new method id and `Z::verify` fails against a proof that was validly produced under the old id.

### Finding Description
The binding that must hold is:
`method_id_used_by_Z::verify(batch_proof_output) == method_id_active_in_BatchProofMethodIdAccessor at the time the batch prover actually generated that proof, for the WHOLE range [start_l2_height, last_l2_height]`.

In `process_complete_proof` (`crates/light-client-prover/src/circuit/mod.rs:288-312`), the method id is chosen only from `batch_proof_output_last_l2_height`: [1](#0-0) 

This does not verify that the same method id has been continuously active since `start_l2_height` of the proof's range — there is no rejection if an entry exists with `start_l2_height < activation_l2_height <= last_l2_height`.

`DataOnDa::BatchProofMethodId` insertion (`crates/light-client-prover/src/circuit/mod.rs:529-565`) only requires a strictly increasing `activation_l2_height` and a valid 3-of-5 council signature — it has no awareness of any batch proof currently in flight: [2](#0-1) 

Independently, the batch prover chooses which method id/ELF to actually run based on the L2 fork/`SpecId` (`fork_from_block_number`), not on `BatchProofMethodIdAccessor`'s table: [3](#0-2) 

and `partition_commitments` only splits proof partitions on `SpecChange` (fork boundaries) or state-diff/count limits, never on `BatchProofMethodIdAccessor` activation heights: [4](#0-3) 

Because these two systems are decoupled, a batch proof can legitimately be produced spanning an L2 range with one method id baked into the ZK receipt, while the light client's activation table gets a new entry with `activation_l2_height` inside that same range before the proof is processed. `binary_search_by_key` on `last_l2_height` will then resolve to the *new* id, and `Z::verify(proof, &new_id, ...)` fails permanently — the proof cannot be regenerated retroactively because the receipt already commits to the old guest binary.

An attacker does not need to forge anything: they only need a copy of an already validly-signed `BatchProofMethodId` transaction (broadcast to the Bitcoin mempool by the council for future activation) and the ability to accelerate its confirmation (fee-bump/CPFP, or simply be the one to relay/mine it), causing it to be included and processed by `run_l1_block` earlier than the council intended — specifically while a batch proof already generated under the old method id, but not yet confirmed/processed, is still in flight and its range straddles the new activation height.

### Impact Explanation
This is a proof-soundness/availability break: a true, correctly generated state transition proof for a real range of sequencer commitments becomes permanently unverifiable by the light client circuit, freezing L2 state progression at the light client (and therefore for Clementine's bridge proof, which consumes the LCP's `l2_state_root`) at the last successfully verified commitment. This matches the "Critical - a true one made unprovable" impact category. The blast radius is the segment of L2 history covered by the stuck proof and everything after it until an operator manually produces a replacement/aggregate proof under the correct-at-generation-time method id (if such recovery is even possible under current logic, since there's no functionality for retroactively supplying an alternate method id per range).

### Likelihood Explanation
Requires: (1) a legitimately 3-of-5-signed `BatchProofMethodId` transaction exists and is broadcast/known before its intended activation, (2) a batch proof is in flight (generated but not yet processed by the light client) whose range straddles the announced `activation_l2_height`, and (3) the attacker can influence DA-block-inclusion timing of the method-id tx relative to the pending proof (fee-bumping a mempool transaction is a normal, low-cost, unprivileged Bitcoin action). Batch proof generation can take a long time, creating a realistic window for this race. This does not require majority hashrate, key compromise, or protocol forgery — only fee-based transaction-ordering influence, which is within the stated attacker capabilities.

### Recommendation
`process_complete_proof` should reject (or defer) any complete proof whose `[start_l2_height, last_l2_height]` range straddles an activation boundary in `BatchProofMethodIdAccessor` — i.e., verify that no entry exists with `start_l2_height < activation_l2_height <= last_l2_height`, and pick/verify the method id based on `start_l2_height` (or explicitly check both ends resolve to the same id) rather than only `last_l2_height`. Additionally, the batch prover should consult the same activation table used by the light client (not just fork/SpecId) when partitioning commitments, ensuring it never produces a single proof whose range crosses a pending method-id activation boundary.

### Proof of Concept
In `crates/light-client-prover/src/tests/mod.rs`:
1. Initialize the LCP with a single initial method id `[0u32;8]` at height 0.
2. Insert sequencer commitments 1 and 2 with `l2_end_block_number` 50 and 150 respectively.
3. Generate a mock `Comp

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

**File:** crates/light-client-prover/src/circuit/mod.rs (L539-565)
```rust
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

**File:** crates/batch-prover/src/rpc.rs (L505-512)
```rust
        let spec_id = fork_from_block_number(last_l2_block.height).spec_id;
        let method_id: [u32; 8] = self
            .context
            .code_commitments
            .get(&spec_id)
            .expect("Spec for L2 block must exist")
            .clone()
            .into();
```

**File:** crates/batch-prover/src/prover.rs (L539-546)
```rust
            // check spec change
            let current_spec = fork_from_block_number(commitment_end_height);
            if current_spec != fork_from_block_number(commitments[i - 1].l2_end_block_number) {
                cumulative_state_diff = commitment_state_diff;
                state.add_partition(i - 1, PartitionReason::SpecChange)?;
                commitments_in_current_partition = 1;
                continue;
            }
```
