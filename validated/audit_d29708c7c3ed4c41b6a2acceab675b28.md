### Title
Global signer state machine agreement uses a floor-rounded 70% threshold while block-signature validation uses a ceiling-rounded threshold, letting `reached_agreement` fire below the true approval requirement - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement`, which decides whether the signer set has reached agreement on the active protocol version, the global burn view, the current-miner/tenure state machine, and the transaction-replay set, computes the 70% threshold with a **floor** division: `vote_weight >= total_weight * 7 / 10`. The consensus-critical block signature threshold used by chainstate validation, `NakamotoBlockHeader::compute_voting_weight_threshold`, computes the same "70%" requirement with a **ceiling**: `(total_weight * 7 + 9) / 10` (implemented via the `ceil` adjustment). For any `total_weight` where `total_weight * 7` is not a multiple of 10, the two thresholds differ by 1, so a vote weight that `reached_agreement` accepts as "70%" can be one unit below what `verify_signer_signatures`/block validation would require. [1](#0-0) [2](#0-1) 

### Finding Description
The two thresholds are meant to encode the same "70% of signer weight" rule, but are computed differently:

- `compute_voting_weight_threshold` (used to validate that a Nakamoto block carries enough signer signatures, i.e. the actual consensus rule enforced by `verify_signer_signatures`) rounds **up**: `threshold = ceil(total_weight * 7 / 10)`. [3](#0-2) 

- `GlobalStateEvaluator::reached_agreement` (used by every signer to decide the *global state machine view*: active protocol version, global burn view, current miner/tenure, and tx-replay set) rounds **down**: `vote_weight >= total_weight * 7 / 10` using integer division, i.e. `floor(total_weight * 7 / 10)`. [1](#0-0) 

For example, with `total_weight = 13`: `compute_voting_weight_threshold` requires `ceil(91/10) = 10`, while `reached_agreement` accepts any `vote_weight >= 9` (since `91/10 = 9` in integer division). A vote weight of exactly `9` (≈69.2%) is therefore treated as "agreement reached" by the global state machine evaluator even though it is below the 70% bar that the same codebase enforces elsewhere for block-signature sufficiency. This is analogous to the `BusLib::ride` bug: the boundary comparison (`>=` against a floor-divided value) admits one more "unit" than the intended capacity/threshold, silently accepting an input that should have been rejected.

Because `reached_agreement` gates `determine_global_burn_view`, `determine_latest_supported_signer_protocol_version`, and `determine_global_state` (which in turn determines the agreed current-miner/tenure state and the transaction-replay set that signers commit to), a weight set that clears the loosened floor-threshold but not the "real" ceiling-threshold lets the signer set lock in a "global state" (current miner, tenure, burn view, tx-replay set) that does not actually reflect true 70% support. Signers acting on this under-supported "agreed" state can pre-commit/sign blocks or miner-invalidations for a tenure that is not genuinely backed by 70% weight — i.e., the equality "signature weight legitimately clears the protocol's 70% bar" is broken by a rounding inconsistency between two independently-implemented threshold functions that are supposed to enforce the same rule. [4](#0-3) 

### Impact Explanation
This is a High severity, minority-triggerable divergence: a subset of signers (with weight below the "true" ceiling-70% threshold but above the buggy floor-70% threshold) can force `reached_agreement` to report consensus on the global state machine (current miner/tenure, burn view, or tx replay set) when the codebase's own block-signature validation would deem that same weight insufficient. This can produce temporary tip/miner disagreement or premature miner-invalidation actions bounded to a specific tenure/round, matching the "minority-triggerable ... divergence, temporary tip disagreement" tier of High impact. It does not directly forge a block (block acceptance itself still uses the correct ceiling threshold in `verify_signer_signatures`), which is why this is not Critical.

### Likelihood Explanation
The rounding discrepancy is deterministic and triggers whenever `total_weight * 7` is not evenly divisible by 10 — a common case for any real-world number of signer weight units — so the divergence window (`floor` vs `ceil` threshold, a gap of 1 weight unit) is reachable on essentially every reward cycle. No majority collusion is required: a minority holding exactly the gap weight (below true 70%, at/above floor 70%) can trigger the divergent "agreement reached" result via ordinary state-machine updates it broadcasts through StackerDB.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement` use the same ceiling-rounding rule as `NakamotoBlockHeader::compute_voting_weight_threshold` (or better, have both call into one shared threshold function) so that "70% agreement" is computed identically everywhere it is checked:

```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    let total = u64::from(self.total_weight);
    let numerator = total.strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD);
    let ceil = if numerator % 10 == 0 { 0 } else { 1 };
    u64::from(vote_weight) >= numerator / 10 + ceil
}
```

### Proof of Concept
1. Configure a signer set with `total_weight = 13` (e.g., weights summing to 13 across several signer addresses) so that `total_weight * 7 = 91`.
2. Have signers whose combined weight equals `9` broadcast identical `StateMachineUpdate`s (matching burn view / current-miner / protocol version).
3. Call `GlobalStateEvaluator::determine_global_state()` / `determine_global_burn_view()` / `determine_latest_supported_signer_protocol_version()`: each calls `reached_agreement(9)`, which returns `true` because `91 / 10 = 9` (floor) and `9 >= 9`.
4. Independently, if the same `9/13` weight were presented as block signatures to `NakamotoBlockHeader::verify_signer_signatures`, `compute_voting_weight_threshold(13)` would return `10` (`ceil(91/10)=10`), and the block would be rejected with `"Not enough signatures. Needed at least 10 but got 9"`.
5. This demonstrates the same weight (9/13 ≈ 69.2%) is accepted as "global agreement reached" by the signer state machine but would be rejected as insufficient signing weight by chainstate block validation — the two "70%" checks disagree at the exact same input. [1](#0-0) [3](#0-2)

### Citations

**File:** libsigner/src/v0/signer_state.rs (L101-158)
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1207)
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
