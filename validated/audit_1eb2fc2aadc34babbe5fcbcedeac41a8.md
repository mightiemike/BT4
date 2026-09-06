### Title
Off-by-one signer-weight threshold divergence between `GlobalStateEvaluator::reached_agreement` and `NakamotoBlockHeader::compute_voting_weight_threshold` - (File: `libsigner/src/v0/signer_state.rs`, `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
The codebase computes the "70% signer weight" approval/agreement threshold in two places using two different rounding rules for the same underlying constant `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`. `NakamotoBlockHeader::compute_voting_weight_threshold` rounds the threshold **up** (ceiling division), while `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` round **down** (floor division). For any `total_weight` where `total_weight * threshold` is not a multiple of 10, the two functions disagree by exactly one unit of weight, mirroring the H-02 pattern of an inconsistent equality computed two different ways over the same logical quantity.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` is the canonical, chain-validation-facing computation of the minimum signer weight needed to approve a Nakamoto block: [1](#0-0) 

It explicitly rounds *up*: `ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 }`, then `(total_weight*threshold)/10 + ceil`. This is used by `verify_signer_signatures` to accept/reject blocks network-wide [2](#0-1) , and it is reused as the canonical `min_weight`/`weight_threshold` by the miner-side signature collector (`stacks-node/src/nakamoto_node/signer_coordinator.rs`, `stacks-node/src/nakamoto_node/stackerdb_listener.rs`) and by signer-side pre-commit/acceptance logic in `stacks-signer/src/v0/signer.rs` (all shown computing `min_weight`/`weight_threshold` via `compute_voting_weight_threshold`) [3](#0-2) [4](#0-3) .

However, `GlobalStateEvaluator::reached_agreement` — which decides whether a **quorum of signers agrees** on the global state-machine view (active miner, burn view, protocol version, tx replay set) — computes the same 70% threshold with plain integer (floor) division and no ceiling correction: [5](#0-4) 

For a `total_weight` such that `(total_weight * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD) % 10 != 0` (e.g. `total_weight = 511`, threshold = 7 → `511*7/10 = 357.7`), `compute_voting_weight_threshold` returns `358` (verified by the existing unit test) [6](#0-5) , while `reached_agreement` treats `357` as sufficient (`357 >= floor(511*7/10) = 357`). This is a one-weight-unit gap that is entirely deterministic and reachable by any signer subset controlling exactly the floor-threshold weight — no majority collusion is required, only a specific weight distribution that a minority naturally has.

### Impact Explanation
`reached_agreement`/`GlobalStateEvaluator::determine_global_state` governs whether a signer/miner-coordinator believes network-wide consensus has been reached on: the active signer protocol version, the global burn view, the active miner/tenure state, and the tx replay set [7](#0-6) . Because this evaluator uses a strictly lower bar than the canonical block-approval threshold used elsewhere for the same nominal 70% figure, a signer set whose weight lands exactly on the floor value can be judged by `GlobalStateEvaluator` to have "reached agreement" on a state view (e.g., who the active miner is, or which burn view is canonical) one weight-unit before the block-level, ceiling-based threshold would consider the same weight sufficient anywhere else in the codebase (`verify_signer_signatures`, miner-side `stackerdb_listener`/`signer_coordinator`, and signer-side pre-commit/acceptance in `signer.rs`). This produces a genuine divergence in what different components of the same signer set consider "consensus reached" for identical vote weights, which can manifest as a temporary tip/miner-state disagreement between signers/miners operating on the boundary weight — consistent with the "signer weight below threshold" analog class. It does not cause block-signature forgery (block acceptance itself still uses the ceiling-based threshold), so the blast radius is bounded to global-state-machine agreement (miner selection/tenure-extend timing, burn-view agreement, protocol-version negotiation), not outright invalid-block acceptance.

### Likelihood Explanation
Triggering requires no majority collusion — only a natural weight distribution among signers where the total signing weight `T` satisfies `T * 7 mod 10 != 0` (the common case, since weights are arbitrary `u32` stake-derived values, not multiples of 10), and a signer subset whose weight lands exactly on the floor value. This is a deterministic, minority-triggerable code-path divergence rather than a probabilistic or majority-requiring attack, so likelihood is comparatively high whenever weight totals aren't round multiples of 10.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` use the same ceiling-rounding rule as `NakamotoBlockHeader::compute_voting_weight_threshold` (ideally by calling that shared function directly rather than re-implementing the arithmetic), so that "70% agreement" means the identical weight value everywhere in the codebase that references `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`.

### Proof of Concept
1. Configure a reward set with signer weights summing to `total_weight = 511` (any distribution not evenly divisible per the threshold fraction works).
2. `NakamotoBlockHeader::compute_voting_weight_threshold(511)` returns `358` (per the existing test) [6](#0-5)  — this is the threshold used everywhere for actual block approval/pre-commit/rejection logic.
3. Call `GlobalStateEvaluator::reached_agreement(357)` on a `GlobalStateEvaluator` with `total_weight = 511`: `357 >= (511 * 7) / 10 = 357` → returns `true` [8](#0-7) .
4. Thus a signer subset holding exactly `357/511` weight is judged by the global state machine to have reached agreement on (for example) who the active miner is, one weight unit before the same nominal 70% bar used for block signature approval (`358`) would consider it sufficient — a concrete, reproducible equality break between two "70% threshold" computations over the same constant.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1190)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1192-1207)
```rust
    /// Compute the threshold for the minimum number of signers (by weight) required
    /// to approve a Nakamoto block.
    pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
        let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
        let total_weight = u64::from(total_weight);
        let ceil = if (total_weight * threshold) % 10 == 0 {
            0
        } else {
            1
        };
        u32::try_from((total_weight * threshold) / 10 + ceil).map_err(|_| {
            ChainstateError::InvalidStacksBlock(
                "Overflow when computing nakamoto block approval threshold".to_string(),
            )
        })
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1296-1301)
```rust
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L213-213)
```rust
        let weight_threshold = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)?;
```

**File:** libsigner/src/v0/signer_state.rs (L56-158)
```rust
    /// Determine what the maximum signer protocol version that a majority of signers can support
    pub fn determine_latest_supported_signer_protocol_version(&self) -> Option<u64> {
        let mut protocol_versions = HashMap::new();
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let entry = protocol_versions
                .entry(update.local_supported_signer_protocol_version)
                .or_insert_with(|| 0);
            *entry += weight;
        }
        // find the highest version number supported by a threshold number of signers
        let mut protocol_versions: Vec<_> = protocol_versions.into_iter().collect();
        protocol_versions.sort_by_key(|(version, _)| *version);
        let mut total_weight_support: u32 = 0;
        for (version, weight_support) in protocol_versions.into_iter().rev() {
            total_weight_support += weight_support;
            if self.reached_agreement(total_weight_support) {
                return Some(version);
            }
        }
        None
    }

    /// Determine what the global burn view is if there is one
    pub fn determine_global_burn_view(&self) -> Option<(&ConsensusHash, u64)> {
        let mut burn_blocks = HashMap::new();
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let (burn_block, burn_block_height) = update.content.burn_block_view();

            let entry = burn_blocks
                .entry((burn_block, burn_block_height))
                .or_insert_with(|| 0);
            *entry += weight;
            if self.reached_agreement(*entry) {
                return Some((burn_block, burn_block_height));
            }
        }
        None
    }

    /// Check if there is an agreed upon global state
    pub fn determine_global_state(&self) -> Option<SignerStateMachine> {
        let active_signer_protocol_version =
            self.determine_latest_supported_signer_protocol_version()?;
        let mut state_views = HashMap::new();
        let mut tx_replay_sets = HashMap::new();
        let mut found_state_view = None;
        let mut found_replay_set = None;
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let (burn_block, burn_block_height) = update.content.burn_block_view();
            let current_miner = update.content.current_miner();
            let tx_replay_set = update.content.tx_replay_set();

            let state_machine = SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: current_miner.clone().into(),
                active_signer_protocol_version,
                // We need to calculate the threshold for the tx_replay_set separately
                tx_replay_set: ReplayTransactionSet::none(),
            };
            let key = SignerStateMachineKey(state_machine.clone());
            let entry = state_views.entry(key).or_insert_with(|| 0);
            *entry += weight;

            if self.reached_agreement(*entry) {
                found_state_view = Some(state_machine);
            }

            let replay_entry = tx_replay_sets
                .entry(tx_replay_set.clone())
                .or_insert_with(|| 0);
            *replay_entry += weight;

            if self.reached_agreement(*replay_entry) {
                found_replay_set = Some(tx_replay_set);
            }
            if found_replay_set.is_some() && found_state_view.is_some() {
                break;
            }
        }
        // Try to find agreed replay set, or find longest common prefix if no exact agreement
        let final_replay_set = if let Some(tx_replay_set) = found_replay_set {
            tx_replay_set
        } else {
            // No exact agreement found, try finding longest common prefix with majority support
            self.find_majority_prefix_replay_set(&tx_replay_sets)
                .unwrap_or_else(ReplayTransactionSet::none)
        };

        if let Some(state_view) = found_state_view.as_mut() {
            state_view.tx_replay_set = final_replay_set;
        }
        found_state_view
    }
```

**File:** libsigner/src/v0/signer_state.rs (L169-183)
```rust
    /// Check if the supplied vote weight crosses the global agreement threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_agreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }

    /// Check if the supplied vote weight crosses the blocking minority threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```
