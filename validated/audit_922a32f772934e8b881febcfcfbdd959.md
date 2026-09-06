### Title
Null-miner override reuses the same sortition index as block-commit winner selection, correlating "who is nullified" with a candidate's position in the burn distribution ordering - (File: stackslib/src/chainstate/burn/sortition.rs)

### Summary
The reported Holograph bug is a class of vulnerability where the *same* random value is reused to make two nested/independent-looking selections (pod, then operator), producing a correlation an attacker can exploit by controlling their position in one of the two selections. Stacks-core has a structurally identical pattern in the epoch 3.x+ ("null miner") sortition-rejection logic: the exact same VRF-derived sortition index used to pick the winning block-commit from `burn_dist` is reused, unmodified, to decide whether that winner is nullified.

### Finding Description
`BlockSnapshot::sample_burn_distribution` computes a single value:

```
let index = sortition_hash.mix_VRF_seed(VRF_seed).to_uint256();
``` [1](#0-0) 

and returns the candidate whose `[range_start, range_end)` contains `index`. [2](#0-1) 

`select_winning_block` calls this once, using `sortition_hash` and `get_last_vrf_seed(...)`, to pick the real winning block-commit from the weighted `burn_dist`. [3](#0-2) 

Separately, `null_miner_wins` is invoked with the **same** `sortition_hash` and independently recomputes `get_last_vrf_seed` from the **same** `block_header` — deterministically returning the identical `vrf_seed` — and calls `sample_burn_distribution` a second time against a two-point distribution `[null_sample_winner (range [0, null_prob_u256)), burn_sample_winner (range [null_prob_u256, MAX))]`. [4](#0-3) 

Because both calls compute the identical `index` from identical inputs, "does the null miner override the winner" is not an independent coin flip conditioned on `atc` — it is deterministically tied to where `index` (and hence the *specific winning candidate's* range) sits relative to the fixed `[0, null_prob_u256)` window. Only whichever candidate's `range_start`/`range_end` interval (assigned by `make_sortition_ranges`, in the order the `burn_sample` vector happens to be built from `commits_with_priors`) overlaps `[0, null_prob_u256)` can ever be nullified; candidates whose entire range lies above `null_prob_u256` can never be nullified for that sortition, no matter how large `atc`'s complement is. [5](#0-4) [6](#0-5) 

This mirrors the Holograph bug exactly: the intended design treats "pick a winner by burn weight" and "possibly override with the null miner" as two independent random decisions, but implementation reuses one shared random value for both, producing bias tied to a party's *position* in the underlying distribution — exactly as the report describes an attacker manipulating pod/position to change their relative odds.

### Impact Explanation
This is a fairness/bias defect in the null-miner mechanism, not a mechanism that causes two honest nodes to disagree — every node runs the identical deterministic computation and will agree on the same "winner" and the same "nullified or not" outcome. Since the rule is applied uniformly and deterministically by all nodes, there is no chain split, no non-reproducible state root, and no case of an invalid block being accepted or a valid block rejected by only some of the network. The practical effect is that certain miners (those whose burn-distribution position falls outside `[0, null_prob_u256)`) are structurally immune to null-override, while whichever miner occupies the low end of the range bears the entire null-override risk for that sortition. This can be leveraged by a miner to reduce (or in some windows increase) their own exposure to null-miner rejection relative to competitors — a fee/incentive-level fairness issue bounded to which miner's already-placed block-commit gets rejected, not a theft of funds or unilateral consensus divergence.

### Likelihood Explanation
Reaching this code path only requires reaching Epoch 3.0+ with the null-miner mechanism active (`null_active` computed from `get_miner_commit_carryover`), which happens periodically whenever total commit carry-over falls below the assumed total. No majority collusion, no admin/privileged key, and no protocol-external assumption is needed — the bias is baked into the deterministic implementation and triggers whenever the null-miner path is exercised, which is a normal, unprivileged/minority-triggerable operating condition (a single miner's own commit ordering already produces this asymmetry).

### Recommendation
Use two independent randomness derivations for the two decisions instead of reusing the same VRF-seed/sortition-hash pair: e.g., derive a second, distinct hash for the null-miner check (for example by mixing in a fixed domain-separation tag before hashing, similar to how `mix_burn_header`/`mix_VRF_seed` already domain-separate other hash mixes), so that "who wins by burn weight" and "is the winner nullified" are statistically independent draws, removing any positional correlation between a candidate's `burn_dist` range and their null-override exposure.

### Proof of Concept
Conceptual demonstration (not requiring privileged access):
1. Suppose `burn_dist` (after `make_sortition_ranges`) places candidate `A`'s range as `[0, R_a)` and candidate `B`'s range as `[R_a, MAX)`, where `R_a > null_prob_u256`.
2. `select_winning_block` computes `index`; if `index < R_a`, `A` wins; otherwise `B` wins.
3. `null_miner_wins` is invoked with the identical `sortition_hash`/`vrf_seed`, thus the identical `index`. It only returns `true` (null overrides) when `index < null_prob_u256`.
4. Since `null_prob_u256 < R_a`, every `index < null_prob_u256` falls inside `A`'s range (`A` is also the winner in that case). Therefore, whenever the null miner overrides, the overridden candidate is deterministically `A`; `B` can never be nullified in this configuration, regardless of `atc`.
5. A miner can influence their relative position in the `burn_sample` vector construction (via commit-chaining order/txid ordering feeding `commits_with_priors`), thereby influencing whether they are the low-range candidate exposed to null-override risk versus a high-range candidate that is structurally immune. [7](#0-6) [8](#0-7)

### Citations

**File:** stackslib/src/chainstate/burn/sortition.rs (L136-149)
```rust
        let index = sortition_hash.mix_VRF_seed(VRF_seed).to_uint256();
        for (i, dist_elem) in dist.iter().enumerate() {
            if (dist_elem.range_start <= index) && (index < dist_elem.range_end) {
                debug!(
                    "Sampled {}: i = {}, sortition index = {}",
                    dist_elem.candidate.block_header_hash, i, &index
                );
                return Some(i);
            }
        }

        // should never happen
        panic!("FATAL ERROR: unable to map {} to a range", index);
    }
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L192-220)
```rust
    fn select_winning_block(
        sort_tx: &mut SortitionHandleTx,
        block_header: &BurnchainBlockHeader,
        sortition_hash: &SortitionHash,
        burn_dist: &[BurnSamplePoint],
    ) -> Result<Option<(LeaderBlockCommitOp, usize)>, db_error> {
        let vrf_seed = Self::get_last_vrf_seed(sort_tx, block_header)?;

        // pick the next winner
        let win_idx_opt =
            BlockSnapshot::sample_burn_distribution(burn_dist, &vrf_seed, sortition_hash);
        match win_idx_opt {
            None => {
                // no winner
                Ok(None)
            }
            Some(win_idx) => {
                // winner!
                Ok(Some((
                    burn_dist
                        .get(win_idx)
                        .expect("FATAL: the block winner index must be in the burn distribution")
                        .candidate
                        .clone(),
                    win_idx,
                )))
            }
        }
    }
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L427-487)
```rust
    fn null_miner_wins(
        sort_tx: &mut SortitionHandleTx,
        block_header: &BurnchainBlockHeader,
        sortition_hash: &SortitionHash,
        commit_winner: &LeaderBlockCommitOp,
        atc: AtcRational,
    ) -> Result<bool, db_error> {
        let vrf_seed = Self::get_last_vrf_seed(sort_tx, block_header)?;

        let mut null_winner = commit_winner.clone();
        null_winner.block_header_hash = {
            // make the block header hash different, to render it different from the winner.
            // Just flip the block header bits.
            let mut bhh_bytes = null_winner.block_header_hash.0;
            for byte in bhh_bytes.iter_mut() {
                *byte = !*byte;
            }
            BlockHeaderHash(bhh_bytes)
        };

        let mut null_sample_winner = BurnSamplePoint::zero(null_winner);
        let mut burn_sample_winner = BurnSamplePoint::zero(commit_winner.clone());

        let null_prob = Self::null_miner_probability(atc);
        let null_prob_u256 = null_prob.into_sortition_probability();

        test_debug!(
            "atc = {}, null_prob = {}, null_prob_u256 = {}, sortition_hash: {}",
            atc.to_hex(),
            null_prob.to_hex(),
            null_prob_u256.to_hex_be(),
            sortition_hash
        );
        null_sample_winner.range_start = Uint256::zero();
        null_sample_winner.range_end = null_prob_u256;

        burn_sample_winner.range_start = null_prob_u256;
        burn_sample_winner.range_end = Uint256::max();

        let burn_dist = [
            // the only fields that matter here are:
            // * range_start
            // * range_end
            // * candidate
            null_sample_winner,
            burn_sample_winner,
        ];

        // pick the next winner
        let Some(win_idx) =
            BlockSnapshot::sample_burn_distribution(&burn_dist, &vrf_seed, sortition_hash)
        else {
            // miner wins by default if there's no winner index
            return Ok(false);
        };

        test_debug!("win_idx = {}", win_idx);

        // null miner is index 0
        Ok(win_idx == 0)
    }
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L713-689)
```rust

```

**File:** stackslib/src/chainstate/burn/distribution.rs (L278-345)
```rust
        let mut burn_sample = commits_with_priors
            .into_iter()
            .map(|mut linked_commits| {
                let all_burns: Vec<_> = linked_commits
                    .iter()
                    .map(|commit| {
                        if let Some(commit) = commit {
                            commit.op.burn_fee() as u128
                        } else {
                            // use 1 as the linked commit min. this gives a miner a _small_
                            //  chance of winning a block even if they haven't performed chained utxos yet
                            1
                        }
                    })
                    .collect();
                let most_recent_burn = all_burns[0];

                let mut sorted_burns = all_burns.clone();
                sorted_burns.sort();
                let median_burn = if window_size % 2 == 0 {
                    (sorted_burns[(window_size / 2) as usize]
                        + sorted_burns[(window_size / 2 - 1) as usize])
                        / 2
                } else {
                    sorted_burns[(window_size / 2) as usize]
                };

                let burns = cmp::min(median_burn, most_recent_burn);

                let frequency = linked_commits.iter().fold(0u8, |count, commit_opt| {
                    if commit_opt.is_some() {
                        count
                            .checked_add(1)
                            .expect("infallable -- commit window exceeds u8::MAX")
                    } else {
                        count
                    }
                });

                let candidate = if let LinkedCommitIdentifier::Valid(op) =
                    linked_commits.remove(0).unwrap().op
                {
                    op
                } else {
                    unreachable!("BUG: first linked commit should always be valid");
                };
                assert_eq!(candidate.burn_fee as u128, most_recent_burn);

                debug!("Burn sample";
                       "txid" => %candidate.txid.to_string(),
                       "most_recent_burn" => %most_recent_burn,
                       "median_burn" => %median_burn,
                       "frequency" => frequency,
                       "all_burns" => %format!("{:?}", all_burns));

                BurnSamplePoint {
                    burns,
                    median_burn,
                    frequency,
                    range_start: Uint256::zero(), // To be filled in
                    range_end: Uint256::zero(),   // To be filled in
                    candidate,
                }
            })
            .collect();

        // calculate burn ranges
        BurnSamplePoint::make_sortition_ranges(&mut burn_sample);
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L379-416)
```rust
    /// Calculate the ranges between 0 and 2**256 - 1 over which each point in the burn sample
    /// applies, so we can later select which block to use.
    fn make_sortition_ranges(burn_sample: &mut Vec<BurnSamplePoint>) {
        if burn_sample.is_empty() {
            // empty sample
            return;
        }
        if burn_sample.len() == 1 {
            // sample that covers the whole range
            let sample_step = burn_sample
                .first_mut()
                .expect("FATAL: expected non-zero burn sample");
            sample_step.range_start = Uint256::zero();
            sample_step.range_end = Uint256::max();
            return;
        }

        // total burns for valid blocks?
        // NOTE: this can't overflow -- there's no way we get that many (u64) burns
        let total_burns_u128 = BurnSamplePoint::get_total_burns(burn_sample).unwrap() as u128;
        let total_burns = Uint512::from_u128(total_burns_u128);

        // determine range start/end for each sample.
        // Use fixed-point math on an unsigned 512-bit number --
        //   * the upper 256 bits are the integer
        //   * the lower 256 bits are the fraction
        // These range fields correspond to ranges in the 32-byte hash space
        let mut burn_acc = Uint512::zero();
        let mut last_sample_range_end = Uint256::zero();
        for sample_step in burn_sample.iter_mut() {
            sample_step.range_start = last_sample_range_end;

            burn_acc = burn_acc + Uint512::from_u128(sample_step.burns);
            sample_step.range_end =
                ((Uint512::from_uint256(&Uint256::max()) * burn_acc) / total_burns).to_uint256();
            last_sample_range_end = sample_step.range_end;
        }

```
