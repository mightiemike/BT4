### Title
`generate_cumulative_witness` gates `last_l1_hash_witness` generation on whether the ENTIRE partition's `short_header_proofs` deque is empty, not on whether the LAST commitment individually needed one, breaking the native/zk parity required for witness verification - ([File: crates/batch-prover/src/prover.rs])

### Summary
In `generate_cumulative_witness`, the decision to populate `last_l1_hash_witness` is made with `if short_header_proofs.is_empty()`, where `short_header_proofs` is accumulated across ALL commitments in the partition [1](#0-0) . If an earlier (non-last) commitment produced a queried hash while the last commitment did not, this global check is non-empty and the witness generation is skipped entirely, even though the last commitment's `cumulative_state_log` at that point genuinely lacks a short header proof for that L2 range.

### Finding Description
The binding that must hold is: *native fills `last_l1_hash_witness` (non-default) whenever the LAST commitment's own contribution to `short_header_proofs` is empty* == *zk's `take_last_queried_hash()` returns `None` for that same last commitment, forcing `run_sequencer_commitments_in_da_slot` to require `get_last_l1_hash_on_contract` with a witness*.

The native side loop iterates over `committed_l2_blocks` per-commitment, calling `SHORT_HEADER_PROOF_PROVIDER.take_queried_hashes(range)` for each commitment's L2 range and pushing results into a single shared `short_header_proofs: VecDeque<Vec<u8>>` accumulator [2](#0-1) . After the loop finishes over ALL commitments, the code checks `if short_header_proofs.is_empty()` — a check over the whole accumulated deque, not scoped to the last commitment specifically — before calling `get_last_l1_hash_on_contract` to populate `last_l1_hash_witness` [3](#0-2) .

This means: if commitment #1 (non-last) contains a `setBlockInfoCall` producing a queried hash, and commitment #2 (last) does not, `short_header_proofs` is non-empty (from commitment #1's contribution) and the witness generation block is skipped, leaving `last_l1_hash_witness` as `Witness::default()`.

On the zk side, `ZkShortHeaderProofProviderService::take_last_queried_hash()` returns the single last-queried hash tracked in `last_queried_and_verified_hash`, which reflects only the most recent query [4](#0-3) . If the guest's per-commitment processing (in `run_sequencer_commitments_in_da_slot`, not directly inspected here due to tool budget) checks this per the LAST commitment to decide whether it must call `get_last_l1_hash_on_contract`, then for the scenario described (SHP only in the earlier commitment) the zk side would find no hash was queried during the last commitment's processing and thus attempt to call `get_last_l1_hash_on_contract`, which requires a populated JMT witness that the native side never generated.

### Impact Explanation
This causes an honest, correct sequencer commitment sequence to become permanently unprovable in the circuit: the guest's witness verification (JMT proof against the last-committed root using `last_l1_hash_witness`) would panic/fail on a default/empty witness, since the native prover skipped population under the flawed global-emptiness check. This matches the Critical impact category "a true [state transition] made unprovable" — repeatable for any commitment set matching the SHP-in-earlier-not-in-last pattern, and would recur deterministically for that partition and any partition mode (`OneByOne`, `Normal`, etc.) touching those commitments.

### Likelihood Explanation
This requires no attacker privilege beyond causing (or waiting for) two sequencer commitments where the first exercises a `setBlockInfoCall` (i.e., a light-client-related system call querying an L1 short header proof) and the second does not. Since `setBlockInfoCall` invocation frequency is influenced by system transactions processed by the sequencer per L2 block and is not attacker-controlled in the traditional sense, likelihood depends on whether such call patterns arise naturally in batch partitioning — plausible whenever the partitioner or block production groups an L1-hash-refreshing block with a later commitment lacking one.

### Recommendation
Scope the `short_header_proofs.is_empty()` check specifically to the LAST commitment's contribution (e.g., track per-commitment counts, or check whether `take_queried_hashes` for only the last commitment's L2 range returned any hash), matching exactly what the zk-side `take_last_queried_hash()` semantics require, rather than checking global emptiness across the whole partition's accumulated `short_header_proofs`.

### Proof of Concept
A `cargo test` in `crates/batch-prover/src/prover.rs` (or an integration test alongside `crates/short-header-proof-provider/tests/shp_integration.rs`) would need to:
1. Construct two sequencer commitments spanning L2 blocks, where the first commitment's L2 blocks include a `setBlockInfoCall` (causing `SHORT_HEADER_PROOF_PROVIDER` to record a queried hash for that range) and the second commitment's L2 blocks do not.
2. Call `generate_cumulative_witness` and assert that `last_l1_hash_witness` is `Witness::default()` (demonstrating the bug) while independently computing whether the LAST commitment alone would have queried a hash (it would not).
3. Compare against the zk-side expectation by asserting `take_last_queried_hash()` returns `None` for the last commitment's replay, which should force `get_last_l1_hash_on_contract` to be invoked and require a non-default witness — showing the mismatch between the two sides' conditions.

Note: I was unable to directly inspect `crates/citrea-stf/src/verifier.rs`'s `run_sequencer_commitments_in_da_slot` logic and the exact per-commitment usage of `take_last_queried_hash()` within the time/tool budget available, so the exact zk-side per-commitment decision logic could not be fully confirmed from source; the native-side global-emptiness check at [3](#0-2)  is confirmed and is the root cause supporting the finding as stated in the question.

### Citations

**File:** crates/batch-prover/src/prover.rs (L992-992)
```rust
    let mut short_header_proofs: VecDeque<Vec<u8>> = VecDeque::new();
```

**File:** crates/batch-prover/src/prover.rs (L1080-1097)
```rust
        let new_hashes = SHORT_HEADER_PROOF_PROVIDER
            .get()
            .unwrap()
            .take_queried_hashes(
                l2_blocks_in_commitment[0].height()
                    ..=l2_blocks_in_commitment
                        .last()
                        .expect("must have at least one")
                        .height(),
            )?;

        for hash in new_hashes {
            let serialized_shp = ledger_db
                .get_short_header_proof_by_l1_hash(&hash)?
                .expect("Should exist");

            short_header_proofs.push_back(serialized_shp);
        }
```

**File:** crates/batch-prover/src/prover.rs (L1102-1117)
```rust
    let mut last_l1_hash_witness = Witness::default();
    // if post tangerine we always need to read the last L1 hash on Bitcoin Light Client contract
    // if the provider have some hashes, circuit will use that.
    if short_header_proofs.is_empty() {
        let cumulative_state_log = cumulative_state_log.unwrap();
        let prover_storage = storage_manager.create_storage_for_l2_height(last_l2_height + 1);

        // we don't care about the return here
        // we only care about the last hash witness getting filled (or not)
        let _ = citrea_stf::verifier::get_last_l1_hash_on_contract::<DefaultContext>(
            cumulative_state_log,
            prover_storage,
            &mut last_l1_hash_witness,
            [0u8; 32], // final state root is only needed for JMT proof verification
        );
    }
```

**File:** crates/short-header-proof-provider/src/zk.rs (L84-86)
```rust
    fn take_last_queried_hash(&self) -> Option<[u8; 32]> {
        self.last_queried_and_verified_hash.borrow_mut().take()
    }
```
