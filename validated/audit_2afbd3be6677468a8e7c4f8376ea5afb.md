### Title
Signer Global-State Consensus Threshold Rounds Down Instead of Up, Allowing Sub-70% Weight to Be Treated as Reached Agreement - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement` computes the 70% supermajority threshold using floor division instead of ceiling division. This is the exact rounding-direction bug class described in the external report: a check that is supposed to enforce a minimum ("you need at least X%") uses `Math.Rounding.Down` where `Math.Rounding.Up` is required, letting an amount that is actually *below* the intended threshold pass the check.

### Finding Description
`GlobalStateEvaluator::reached_agreement` is: [1](#0-0) 

```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
```

This computes `floor(total_weight * 7 / 10)` and accepts any `vote_weight` that is `>=` that floored value. Whenever `total_weight * 7` is not exactly divisible by 10, the floored bound is strictly smaller than the true 70% cutoff. For example, with `total_weight = 13`: the true 70% bound is `9.1`, so a genuine supermajority requires `10`. The buggy check computes `floor(9.1) = 9` and accepts `vote_weight = 9` (≈69.23%), i.e. a coalition holding strictly less than 70% of signer weight is nonetheless reported as having "reached agreement."

This is the same rounding class the report flags, but the codebase itself demonstrates the *correct* pattern elsewhere for the identical 70% concept: `NakamotoBlockHeader::compute_voting_weight_threshold`, which is used to actually verify a Nakamoto block's signer signatures, deliberately rounds **up**: [2](#0-1) 

```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 {
        0
    } else {
        1
    };
    u32::try_from((total_weight * threshold) / 10 + ceil).map_err(|_| { ... })
}
```

`reached_disagreement` has the mirror-image problem for the 30% blocking-minority side: [3](#0-2) 

`GlobalStateEvaluator::reached_agreement` (and thus `determine_global_state`, `determine_global_burn_view`, and `determine_latest_supported_signer_protocol_version`) is what the signer software uses to decide the network's agreed-upon current miner, burn-chain view, protocol version and tx-replay set: [4](#0-3) [5](#0-4) 

These evaluators are wired into the live signer runloop via `GlobalStateEvaluator` imported and used in `stacks-node/src/nakamoto_node/stackerdb_listener.rs` and `stacks-signer/src/v0/signer.rs`, which drive miner/tenure decisions.

### Impact Explanation
The intended protocol invariant is a single 70% weight bar, and `NakamotoBlockHeader::compute_voting_weight_threshold` shows the codebase's own correct implementation of it (ceiling). `GlobalStateEvaluator::reached_agreement`, used for the signer set's internal notion of "global agreement" on burn view / current miner / protocol version / tx replay set, implements a *systematically weaker* bar (floor) for the same nominal 70% concept. Whenever `total_weight` isn't a clean multiple of 10 (the common case, since weight is apportioned via largest-remainder methods and not guaranteed to divide evenly), a coalition just under the true 70% cutoff is treated by every signer running this code as having produced the network's canonical global state. Because this determines which miner/tenure/burn view signers believe is canonical, a coalition below the intended supermajority can steer the signer set's shared notion of "current miner"/burn view away from what a genuine 70%-weighted decision would require, which can manifest as signers disagreeing with the node/coordinator's block-header-level (correctly-rounded) 70% threshold — i.e., temporary tip/state disagreement between the signer set's internal consensus and the actual block-acceptance consensus.

### Likelihood Explanation
This triggers deterministically any time `total_weight * 7 mod 10 != 0` (most reward cycles, since signer weight is an integer apportionment of stacked STX/slots and rarely lands on an exact multiple of 10). No privileged access or majority coalition is required — the affected coalition only needs the (already slightly-less-than-70%) weight that this floor computation happens to accept, which is inherently smaller than a true supermajority.

### Recommendation
Change `reached_agreement` (and the corresponding boundary in `reached_disagreement`) to use the same ceiling-rounding scheme as `NakamotoBlockHeader::compute_voting_weight_threshold`, e.g. `threshold = (total_weight as u64 * NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD).div_ceil(10)`, so that "reached agreement" strictly requires at least the true 70% supermajority, consistent with the block-header verification path.

### Proof of Concept
1. Configure a signer set whose weights sum to `total_weight = 13` (any non-multiple-of-10 total works analogously).
2. A subset of signers controlling `vote_weight = 9` (69.23% of total) submits matching `StateMachineUpdate`s (e.g., same `burn_block_view`/`current_miner`).
3. `GlobalStateEvaluator::reached_agreement(9)` computes `floor(13*7/10) = 9` and returns `true`, so `determine_global_state`/`determine_global_burn_view` report this sub-70%-weight view as the network's agreed global state, even though `NakamotoBlockHeader::compute_voting_weight_threshold(13) = ceil(9.1) = 10` would correctly reject `9` as insufficient for the same nominal 70% bar used at block-acceptance time.

### Citations

**File:** libsigner/src/v0/signer_state.rs (L81-99)
```rust
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

**File:** libsigner/src/v0/signer_state.rs (L169-175)
```rust
    /// Check if the supplied vote weight crosses the global agreement threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_agreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
    }
```

**File:** libsigner/src/v0/signer_state.rs (L177-183)
```rust
    /// Check if the supplied vote weight crosses the blocking minority threshold.
    /// Returns true if it has, false otherwise.
    pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
        u64::from(vote_weight)
            > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
                / 10
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
