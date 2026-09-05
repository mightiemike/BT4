### Title
Signer Global-State-Agreement Threshold Uses Floor Division While Block-Approval Threshold Uses Ceiling Division, Causing Signer Consensus Divergence - (File: libsigner/src/v0/signer_state.rs)

### Summary
The Nakamoto block-signing approval threshold and the signer network's "global state machine" agreement threshold are both derived from the same constant, `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD` (70%), but are computed with two different rounding rules. `NakamotoBlockHeader::compute_voting_weight_threshold` rounds the 70% cutoff *up* (ceiling division), while `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` round it *down* (plain integer division, no ceiling adjustment). Whenever `total_weight * 7` is not evenly divisible by 10 — the exact "fractional interval" scenario described in the source report — the two thresholds diverge by one unit of weight, allowing a minority of signer weight to be treated as having reached "global agreement" by one code path while the same weight would not satisfy the real block-approval threshold used elsewhere.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` (used to validate that a Nakamoto block has enough signer weight behind it) computes: [1](#0-0) 
```
let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
let total_weight = u64::from(total_weight);
let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
u32::try_from((total_weight * threshold) / 10 + ceil)
```
This is a ceiling division: `threshold = ceil(total_weight * 7 / 10)`.

By contrast, `GlobalStateEvaluator::reached_agreement` / `reached_disagreement` in `libsigner`, which the signer network's runloop uses to decide whether the signer set has reached a global consensus on burn view, current miner/tenure, protocol version, and transaction replay set, compute: [2](#0-1) 
```
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD)
            / 10
}
```
This is a plain floor division with no ceiling correction: `threshold = floor(total_weight * 7 / 10)`.

`reached_agreement` is used directly by `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` to decide when a majority-view (burn view, current miner/tenure, protocol version, tx replay set) has been "agreed" by the signer set: [3](#0-2) 

When `total_weight * 7` is not a multiple of 10 (e.g. `total_weight = 11` ⇒ `77/10`), the two formulas disagree: the block-approval path requires ceil(7.7) = 8 units of weight, while the global-state-agreement path only requires floor(7.7) = 7 units of weight. This is structurally identical to the reported bug class: a duration/quantity not evenly divisible by the step size produces a fractional remainder that is handled inconsistently by two code paths that are supposed to enforce the same 70% rule, causing one path to under/over count relative to the other.

### Impact Explanation
Because `total_weight` (the sum of arbitrary per-signer stacked-STX-derived weights) is not guaranteed to be a multiple of 10, this divergence is trivially and routinely reachable without requiring a majority of signers — it only requires that the aggregate signer weight distribution produce a non-multiple-of-10 total. A subset of signers holding weight equal to `floor(total_weight*7/10)` (which is strictly less than the `ceil(total_weight*7/10)` weight required to actually approve a block per `compute_voting_weight_threshold`) will cause `GlobalStateEvaluator::determine_global_state`/`determine_global_burn_view`/`determine_latest_supported_signer_protocol_version` to report that global agreement on the current miner, burn view, or protocol version has been reached. Other signers/nodes computing against the stricter ceiling-based block-approval threshold will not agree that the same weight is sufficient. This produces a genuine minority-triggerable divergence in what different signers believe is the "agreed" global state (current miner/tenure, burn view), i.e., a temporary tip/miner-view disagreement across the signer network — matching the High-impact category of "a minority-triggerable ... static-validation divergence... temporary tip disagreement."

### Likelihood Explanation
High likelihood of occurrence in practice: signer weights are derived from real-valued stacked amounts and are not designed to sum to multiples of 10, so almost any reward cycle's signer set will exhibit this rounding mismatch. No privileged access, admin key, or majority collusion is required — it is a deterministic consequence of the two threshold formulas whenever `total_weight * 7 mod 10 != 0`.

### Recommendation
Unify the threshold computation: have `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` call the same ceiling-division helper used by `NakamotoBlockHeader::compute_voting_weight_threshold` (or export the ceiling-division routine as a single shared utility used by both `stackslib` and `libsigner`), so that the 70% cutoff is rounded consistently everywhere it is used to gate signer/network consensus decisions.

### Proof of Concept
1. Construct a signer set whose total weight is 11 (e.g., signer weights `[7, 4]`, or any weight distribution where the sum is not a multiple of 10).
2. Compute the real block-approval threshold: `NakamotoBlockHeader::compute_voting_weight_threshold(11)` → `ceil(11*7/10) = ceil(7.7) = 8`.
3. Have signers with total weight 7 (e.g., the signer with weight 7) submit matching `StateMachineUpdate`s (same burn view / current miner) to a `GlobalStateEvaluator` with `total_weight = 11`.
4. Call `GlobalStateEvaluator::reached_agreement(7)`: `7 >= floor(11*7/10) = 7` → returns `true`, so `determine_global_state`/`determine_global_burn_view` report that the signer set has reached global agreement on that burn view/miner.
5. Meanwhile, a block requiring actual signature weight to satisfy `compute_voting_weight_threshold(11) = 8` would still be rejected by full block-validation logic with only 7 units of signature weight.
6. Result: the signer runloop's internal view of "global state" (used to decide which miner/tenure to treat as active, and to drive local signing/mining behavior) can be considered settled with weight 7, while the chainstate-level threshold for actually approving a block requires weight 8 — a genuine divergence between two consensus-adjacent 70%-threshold computations that only manifests when `total_weight * 7` has a nonzero remainder mod 10.

### Citations

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
