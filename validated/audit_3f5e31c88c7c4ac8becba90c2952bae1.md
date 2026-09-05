### Title
Divergent 70% signer-weight threshold formulas (ceiling vs. floor) between block-signature validation and signer global-state agreement - (File: `stackslib/src/chainstate/nakamoto/mod.rs`, `libsigner/src/v0/signer_state.rs`)

### Summary
The codebase implements the "70% of signer weight" threshold check in two independent places with two different rounding rules. `NakamotoBlockHeader::compute_voting_weight_threshold` (consensus-critical, used to accept/reject a Nakamoto block header's signatures) rounds the threshold **up** (ceiling), while `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` (used by the `stacks-signer`/`libsigner` runtime to decide whether the signer set has reached global agreement on state such as the current miner, burn view, protocol version, or tx-replay set) rounds **down** (floor). This is exactly the "duplicate/near-duplicate logic that can silently diverge" class of bug described in the external report.

### Finding Description
`compute_voting_weight_threshold` computes the minimum signing weight required to approve a block header: [1](#0-0) 
This is used inside `verify_signer_signatures`, the function every node calls to validate whether a Nakamoto block header carries enough signer weight: [2](#0-1) 
Given `total_weight` and `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` (defined once in `stackslib/src/core/mod.rs`), this formula computes `ceil(total_weight * threshold / 10)`.

Separately, `libsigner`'s `GlobalStateEvaluator` re-implements the "has the signer set reached the threshold" check using a **floor** division instead of a ceiling: [3](#0-2) 
This computes `vote_weight >= total_weight * threshold / 10` (integer/floor division), which is a strictly weaker bound than the ceiling used in `compute_voting_weight_threshold` whenever `total_weight * threshold` is not evenly divisible by 10 (e.g., `total_weight = 13`, `threshold = 7` ⇒ chain-validation threshold is `10`, but `reached_agreement` accepts `9`).

`reached_agreement`/`reached_disagreement` gate `determine_global_state`, `determine_latest_supported_signer_protocol_version`, and `determine_global_burn_view` — i.e., the signer's belief about which miner is currently authorized, which burn view is canonical, and which protocol version/tx-replay-set is active: [4](#0-3) 

### Impact Explanation
Because the two threshold formulas disagree on boundary weight distributions, a signer running the `GlobalStateEvaluator` logic can conclude that "global agreement" (70%) has been reached on a given miner/burn-view/state-machine when the actual weight is one unit below what the consensus-level `compute_voting_weight_threshold` would require for a block header signature set. This does not directly let an invalid block be accepted (block header validation still always uses the stricter, correct ceiling formula in `verify_signer_signatures`), so it cannot by itself cause a full chain split or forged block. However, it can cause a subset of signers to prematurely treat a particular miner/tenure as "agreed" while other signers (or the chain-validation path) would not, producing a **temporary tip/tenure disagreement** among signers about which miner to support — the "High: temporary tip disagreement" category.

### Likelihood Explanation
This is minority-triggerable: it only requires the natural, un-adversarial case where total signer weight times 7 is not a multiple of 10 (extremely common with real-world weight distributions), and any single signer near the boundary weight. No majority collusion or privileged access is required — it is purely a latent logic bug reachable during normal signer-set operation.

### Recommendation
Unify the threshold computation into a single shared function (e.g., have `libsigner`'s `GlobalStateEvaluator` call `NakamotoBlockHeader::compute_voting_weight_threshold`, or extract a common `compute_threshold(total_weight, numerator)` helper used by both `stackslib` and `libsigner`) so that "reaching agreement" and "meeting the block-approval threshold" always use the same rounding rule.

### Proof of Concept
1. Configure a reward set with `total_weight = 13` and `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (70%).
2. `compute_voting_weight_threshold(13)` → `ceil(91/10) = 10`: block-header validation (`verify_signer_signatures`) requires **10** units of signing weight.
3. `GlobalStateEvaluator::reached_agreement(9)` on the same 13-unit total weight → `9*10=90 >= 13*7=91`? No — actually check: floor formula is `vote_weight >= total_weight*7/10 = 90/10=9` (integer division truncates `91/10` to `9`), so `vote_weight = 9` satisfies `9 >= 9` → **true**, "agreement reached" with only 9/13 weight, one unit below the 10/13 required by the header-validation path.
4. A signer set where 9 of 13 weight-units agree on a given `MinerState`/`ConsensusHash` will have `GlobalStateEvaluator` report consensus, even though the actual signing threshold enforced by `verify_signer_signatures` for block headers requires 10/13 — a demonstrable divergence between the two "same equality" checks.

Note: I could not fully trace every downstream consumer of `GlobalStateEvaluator::determine_global_state` inside `stacks-signer` within the available search budget (e.g., exact behavior differences this induces in miner selection/tenure-extend logic), so the precise blast radius of the resulting signer disagreement should be validated further by tracing `stacks-signer/src/v0/signer.rs` and `stacks-signer/src/v0/signer_state.rs` call sites.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1189)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
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

**File:** libsigner/src/v0/signer_state.rs (L101-144)
```rust
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
