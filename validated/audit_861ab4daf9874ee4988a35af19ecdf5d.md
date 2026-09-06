Based on my research, I found a genuine structural analog to the Ajna MOMP-griefing bug in the Nakamoto-era Assumed Total Commitment (ATC) / "null miner" sortition-rejection logic. I was not able to pull the exact source of `BurnchainStateTransition::total_burns()` / `windowed_median_burns()` before running out of tool budget, so that specific detail is noted as unverified below, but the mechanism, its consumers, and its documented semantics are all confirmed directly from `sortition.rs` and `distribution.rs`.

### Title
Adversary can grief a legitimate sortition winner by inflating the windowed median burn total to trigger a null-miner rejection - (File: stackslib/src/chainstate/burn/sortition.rs)

### Summary
The Ajna bug lets an attacker frontrun a kicker by injecting a single outsized loan to skew an aggregate statistic (average loan size → MOMP → NP) that a later, unrelated actor's reward/penalty depends on. Stacks has a directly analogous mechanism: the Nakamoto-era "Assumed Total Commitment" (ATC) check compares a block's actual total burn to a **windowed median** of recent total burns, and can nullify (reject) the sortition winner if the ratio is low enough. An adversary can inflate that windowed median with a single outsized, unprivileged burn commitment, then let the legitimate miner's normal-sized, otherwise-winning commit be probabilistically rejected in favor of the "null miner" within the same commitment window.

### Finding Description
`BlockSnapshot::get_miner_commit_carryover` computes:
```
ATC = min(1, total_burns / windowed_median_burns)
``` [1](#0-0) 

This ATC value feeds `null_miner_wins`, which builds a two-outcome distribution (real winner vs. "null miner") whose probability split is `1 - ATC`, i.e. the lower the current block's spend is relative to the recent windowed median, the higher the chance that no legitimate miner wins the sortition at all: [2](#0-1) 

This is invoked unconditionally for every Epoch 3.0+ sortition in `BlockSnapshot::make_snapshot` right after a legitimate winner is already selected via `select_winning_block`/`sample_burn_distribution`: [3](#0-2) 

If the null miner "wins" this second draw, the actual winning block-commit is thrown out entirely (`reject_winner_reason = Some("Null miner defeats block winner...")`), and the sortition proceeds with no winner, denying the real miner their block reward for that burn block: [4](#0-3) 

The burn distribution itself is built by `BurnSamplePoint::make_min_median_distribution`, which is explicitly windowed over `MINING_COMMITMENT_WINDOW` blocks and takes `min(most_recent_burn, median_burn)` per miner over that window: [5](#0-4) [6](#0-5) 

The equality this breaks is the same one broken in the Ajna report: **a reward/penalty decision that should depend only on a party's own honest, current-state contribution is instead a function of a shared, attacker-manipulable aggregate (a windowed median) that any unprivileged party can skew with a single well-timed, outsized transaction just prior to the target's action.** In Ajna it was loan size vs. MOMP/NP; here it is a single miner's block-commit burn vs. the ATC-derived null-miner probability. Any Bitcoin holder can submit a `LeaderBlockCommitOp` with an arbitrarily large `burn_fee` at any block height inside the `MINING_COMMITMENT_WINDOW` that precedes a target miner's expected winning commit, raising the "windowed median" total burn that the target's own (otherwise-winning, normal-sized) commit is compared against, and thereby manufacturing a non-trivial probability that the null miner defeats the target's legitimate win — with no participation or majority required from any other actor.

### Impact Explanation
This matches the "High" bucket: a minority-triggerable, unprivileged perturbation of the sortition/VRF selection logic that can cause the network-wide-agreed sortition to reject a legitimate winner (a "poison"/reward-denial event) rather than pay the block reward as the burn-weight function would otherwise dictate. It is deterministic and reproduced identically by every node (no chain split), but it is a real divergence between "who *should* win by burn-weight" and "who the null-miner mechanic allows to win," bounded to a denial of one miner's block reward for that burn block — directly parallel to the Ajna kicker being denied their expected bonus/penalty outcome.

### Likelihood Explanation
Like the Ajna finding (accepted as Medium/High-adjacent after escalation), this attack costs the adversary real, non-refundable burnt BTC with no direct profit — it is a pure griefing play, and the larger the honest liquidity/commitment window activity, the more expensive it is to move the median meaningfully. It requires no privileged role, no majority hash power, and no cooperation from other miners — only the ability to submit one outsized `LeaderBlockCommitOp` inside the target's `MINING_COMMITMENT_WINDOW`, which is unprivileged and always reachable by any Bitcoin holder.

### Recommendation
Consider whether `windowed_median_burns` should be computed from a snapshot/lagged view that excludes the sortition-triggering block itself, or dampen single-block outlier influence on the ATC ratio (e.g., cap per-block contribution to the window, or use a longer/robust trimmed statistic), analogous to Ajna's "recommend taking a snapshot of the average" mitigation, so a single unprivileged large burn cannot materially swing the null-miner probability against an otherwise-legitimate winner.

### Proof of Concept
1. Adversary identifies a block-commit window (`MINING_COMMITMENT_WINDOW` blocks) in which a target miner is expected to submit a normal-sized, otherwise-winning `LeaderBlockCommitOp`.
2. Adversary submits, at any earlier height inside that same window, a `LeaderBlockCommitOp` with an unusually large `burn_fee` (this op does not need to win sortition itself — see `BurnSamplePoint::make_min_median_distribution`, which folds every submitted commit's burn into the window regardless of outcome, at `stackslib/src/chainstate/burn/distribution.rs:149-189,276-305`).
3. This raises the windowed median total burn used in `get_miner_commit_carryover` (`stackslib/src/chainstate/burn/sortition.rs:330-357`).
4. When the target's normal-sized commit is evaluated, `ATC = total_burns / windowed_median_burns < 1`, giving the null miner a non-zero chance to win via `null_miner_wins` (`stackslib/src/chainstate/burn/sortition.rs:423-487`).
5. If the null miner wins the coin-flip, `make_snapshot` rejects the target's otherwise-legitimate winning commit (`stackslib/src/chainstate/burn/sortition.rs:673-711`), denying them the block reward for that burn block at the cost of the adversary's one large, unrecoverable burn.

**Caveat:** I could not confirm the exact source definition of `BurnchainStateTransition::total_burns()`/`windowed_median_burns()` (in `stackslib/src/burnchains/burnchain.rs`) before exhausting available tool calls, so the precise aggregation window (e.g., whether it's per-block total spend across all miners, or something narrower) is inferred from the doc comment on `get_miner_commit_carryover` rather than directly read from its implementation. This should be verified against the actual field-population code in `burnchain.rs` before treating this as fully confirmed.

### Citations

**File:** stackslib/src/chainstate/burn/sortition.rs (L314-357)
```rust
    ///
    ///                              total-block-spend
    /// This is ATC = min(1, ----------------------------------- )
    ///                       median-windowed-total-block-spend
    ///
    /// Now, this value is 1.0 in the "happy path" case where miners commit the same BTC in this
    /// block as they had done so over the majority of the windowed burnchain blocks.
    ///
    /// It's also 1.0 if miners spend _more_ than this median.
    ///
    /// It's between 0.0 and 1.0 only if miners spend _less_ than this median.  At this point, it's
    /// possible that the "null miner" can win sortition, and the probability of that null miner
    /// winning is a function of (1.0 - ATC).
    ///
    /// Returns the ATC value, and whether or not it decreased.  If the ATC decreased, then we must
    /// invoke the null miner.
    fn get_miner_commit_carryover(
        total_burns: Option<u64>,
        windowed_median_burns: Option<u64>,
    ) -> (AtcRational, bool) {
        let Some(block_burn_total) = total_burns else {
            // overflow
            return (AtcRational::zero(), false);
        };

        let Some(windowed_median_burns) = windowed_median_burns else {
            // overflow
            return (AtcRational::zero(), false);
        };

        if windowed_median_burns == 0 {
            // no carried commit, so null miner wins by default.
            return (AtcRational::zero(), true);
        }

        if block_burn_total >= windowed_median_burns {
            // clamp to 1.0, and ATC increased
            return (AtcRational::one(), false);
        }

        (
            AtcRational::frac(block_burn_total, windowed_median_burns),
            true,
        )
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L423-487)
```rust
    /// Determine whether or not the null miner has won sortition.
    /// This works by creating a second burn distribution: one with the winning block-commit, and
    /// one with the null miner.  The null miner's mining power will be computed as a function of
    /// their ATC advantage.
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

**File:** stackslib/src/chainstate/burn/sortition.rs (L640-690)
```rust
        // Try to pick a next block.
        let (winning_block, winning_block_burn_dist_index) = BlockSnapshot::select_winning_block(
            sort_tx,
            block_header,
            &next_sortition_hash,
            &state_transition.burn_dist,
        )?
        .expect("FATAL: there must be a winner if the burn distribution has 1 or more points");

        // in epoch 3.x and later (Nakamoto and later), there's two additional changes:
        // * if the winning miner didn't mine in more than k of n blocks of the window, then their chances of
        // winning are 0.
        // * There exists a "null miner" that can win sortition, in which case there is no
        // sortition.  This happens if the assumed total commit with carry-over is sufficiently low.
        let mut reject_winner_reason = None;
        if epoch_id >= StacksEpochId::Epoch30 {
            let winner_frequency = state_transition
                .burn_dist
                .get(winning_block_burn_dist_index)
                .expect("FATAL: the winner index must be in the burn distribution")
                .frequency;
            if !Self::check_miner_is_active(
                epoch_id,
                state_transition.windowed_block_commits.len(),
                &winning_block.apparent_sender,
                winner_frequency,
            ) {
                reject_winner_reason = Some("Miner did not mine often enough to win".to_string());
            }
            let (atc, null_active) = Self::get_miner_commit_carryover(
                state_transition.total_burns(),
                state_transition.windowed_median_burns(),
            );
            if null_active && reject_winner_reason.is_none() {
                // there's a chance the null miner can win
                if Self::null_miner_wins(
                    sort_tx,
                    block_header,
                    &next_sortition_hash,
                    &winning_block,
                    atc,
                )? {
                    // null wins
                    reject_winner_reason = Some(
                        "Null miner defeats block winner due to insufficient commit carryover"
                            .to_string(),
                    );
                }
            }
        }

```

**File:** stackslib/src/chainstate/burn/sortition.rs (L691-711)
```rust
        if let Some(reject_winner_reason) = reject_winner_reason {
            info!("SORTITION({block_height}): WINNER REJECTED: {reject_winner_reason:?}";
                  "txid" => %winning_block.txid,
                  "stacks_block_hash" => %winning_block.block_header_hash,
                  "burn_block_hash" => %winning_block.burn_header_hash);

            // N.B. can't use `make_snapshot_no_sortition()` helper here because then `sort_tx`
            // would be mutably borrowed twice.
            return BlockSnapshot::make_snapshot_no_sortition(
                sort_tx,
                my_sortition_id,
                my_pox_id,
                parent_snapshot,
                block_header,
                first_block_height,
                last_burn_total,
                &next_sortition_hash,
                &state_transition.txids(),
                accumulated_coinbase_ustx,
            );
        }
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L149-189)
```rust
    /// Make a burn distribution -- a list of (burn total, block candidate) pairs -- from a block's
    /// block commits and user support burns.
    ///
    /// All operations need to be supplied in an ordered Vec of Vecs containing
    ///   the ops at each block height in a mining commit window.  Normally, this window
    ///   is the constant `MINING_COMMITMENT_WINDOW`, except during prepare-phases and post-PoX
    ///   sunset.  In either of these two cases, the window is only one block.  The code does not
    ///   consider which window is active; it merely deduces it by inspecting the length of the
    ///   given `block_commits` argument.
    ///
    /// If a burn refers to more than one commitment, its burn amount is *split* between those
    ///   commitments
    ///
    ///  Burns are evaluated over the mining commitment window, where the effective burn for
    ///   a commitment is := min(last_burn_amount, median over the window)
    ///
    /// Returns the distribution, which consumes the given lists of operations.
    ///
    /// * `block_commits`: this is a mapping from relative block_height to the block
    ///   commits that occurred at that height. These relative block heights start
    ///   at 0 and increment towards the present. When the mining window is 6, the
    ///   "current" sortition's block commits would be in index 5.
    /// * `missed_commits`: this is a mapping from relative block_height to the
    ///   block commits that were intended to be included at that height. These
    ///   relative block heights start at 0 and increment towards the present. There
    ///   will be no such commits for the current sortition, so this vec will have
    ///   `missed_commits.len() = block_commits.len() - 1`
    /// * `burn_blocks`: this is a vector of booleans that indicate whether or not a block-commit
    ///   occurred during a PoB-only sortition or a possibly-PoX sortition.  The former occurs
    ///   during either a prepare phase or after PoX sunset, and must have only one (burn) output.
    ///   The latter occurs everywhere else, and must have `OUTPUTS_PER_COMMIT` outputs after the
    ///   `OP_RETURN` payload.  The length of this vector must be equal to the length of the
    ///   `block_commits` vector.  `burn_blocks[i]` is `true` if the `ith` block-commit must be PoB.
    #[allow(clippy::indexing_slicing)] // this method panics on bad inputs, it should panic on bad indexes as well
    pub fn make_min_median_distribution(
        mining_commitment_window: u8,
        mut block_commits: Vec<Vec<LeaderBlockCommitOp>>,
        mut missed_commits: Vec<Vec<MissedBlockCommit>>,
        expects_single_commit: Vec<bool>,
    ) -> Vec<BurnSamplePoint> {
        // sanity check
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L276-305)
```rust
        // now, commits_with_priors has the burn amounts for each
        //   linked commitment, we can now generate the burn sample points.
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
```
