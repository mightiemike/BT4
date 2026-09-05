### Title
Signer-side supermajority threshold uses floor division while consensus-critical block-approval threshold uses ceiling division, causing signer global-state agreement below the true 70% weight requirement - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`NakamotoBlockHeader::compute_voting_weight_threshold` (the consensus-critical function that nodes use to accept/reject a Nakamoto block's signer signatures) rounds the 70% weight threshold **up** (ceiling), while `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` (used by the signer set to converge on global state: active protocol version, global burn view, current miner, and tx replay set) computes the *same conceptual* 70%/30% thresholds using **floor** division. This is directly analogous to the reported bug class: two code paths independently re-implement a value that is supposed to be identical, and they diverge.

### Finding Description
The canonical, consensus-enforced threshold is computed in: [1](#0-0) 
```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
}
```
This is used by `verify_signer_signatures` — the function every node in the network runs to decide whether a Nakamoto block's aggregate signer weight is sufficient to accept the block: [2](#0-1) 

A regression test explicitly pins the ceiling behavior (e.g. `total_weight=511 -> threshold=358`, not 357): [3](#0-2) 

By contrast, the signer-set's own global-state consensus mechanism computes the "same" 70%/30% thresholds using floor division and a strict `>` for disagreement: [4](#0-3) 
```rust
pub fn reached_agreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        >= u64::from(self.total_weight).strict_mul(NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD) / 10
}
pub fn reached_disagreement(&self, vote_weight: u32) -> bool {
    u64::from(vote_weight)
        > u64::from(self.total_weight).strict_mul(10 - NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD) / 10
}
```
For any `total_weight` where `total_weight * 7 % 10 != 0` (e.g. `total_weight = 511`), `reached_agreement` requires only `floor(511*7/10) = 357`, while the consensus-critical block-approval threshold requires `358`. `reached_agreement` is used to decide `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, and `determine_global_state` (miner state and tx replay set agreement) inside `GlobalStateEvaluator`: [5](#0-4) [6](#0-5) 

Both computations are meant to express the same protocol-level constant, `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7` (i.e. 70%), but one file rounds up and the other rounds down — exactly the "two definitions of the same key/constant disagree" bug class from the external report (`EXPECTED_SYSTEM_CONTRACT_UPGRADE_TX_HASH_KEY` = 9 vs. `protocolUpgradeTxHashKey()` returning 7).

### Impact Explanation
Signers use `GlobalStateEvaluator::reached_agreement` to decide what the network-wide "current miner", "global burn view", and "active signer protocol version" are — inputs that directly drive which tenure/miner a signer treats as canonical and which blocks it will sign. Because `reached_agreement` accepts a strictly *smaller* weight than the true 70% threshold enforced on-chain (`compute_voting_weight_threshold`/`verify_signer_signatures`), a set of signers holding just under the real 70% supermajority (but at or above the floor-rounded value) can cause the signer set to converge on a "global state" that does not actually correspond to a 70%-weight supermajority as required by the rest of the protocol. This creates a genuine equality break: the value that decision-making logic treats as "supermajority reached" is not identical to the value the chain-level block-approval logic treats as "supermajority reached," for the same nominal weight distribution. This can produce temporary disagreement between the signer set's internal state-machine consensus and the actual block-approval requirement, which is a High-severity, minority-triggerable static-threshold divergence bounded to signer-state convergence (not outright chain-split, since block acceptance itself still uses the correct ceiling function).

### Likelihood Explanation
This is deterministic and requires no privileged access: it is a pure integer-math discrepancy that manifests whenever `total_weight * 7 mod 10 != 0` — the common case for the vast majority of `total_weight` values (only 1 out of every 10 possible remainders avoids the discrepancy). Any set of signers whose combined weight equals `floor(total_weight*7/10)` (rather than the true `ceil(total_weight*7/10)`) will trigger the divergence with zero additional cost or coordination beyond normal signer participation.

### Recommendation
Make `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` delegate to `NakamotoBlockHeader::compute_voting_weight_threshold` (or otherwise apply the identical ceiling-rounding logic) so that the signer-state supermajority calculation is provably identical to the chain-enforced block-approval threshold. Add a shared/single source of truth for "what does 70% of `total_weight` mean" instead of two independent re-implementations in `stackslib/src/chainstate/nakamoto/mod.rs` and `libsigner/src/v0/signer_state.rs`.

### Proof of Concept
1. Configure a reward-cycle signer set with `total_weight = 511` (achievable with weights like `73, 73, 73, 73, 73, 73, 73` = 511, or any combination summing to 511).
2. Per `compute_voting_weight_threshold(511)` (used by `verify_signer_signatures` and thus by every node when accepting a block), the required weight to approve a block is `358` — confirmed by the existing unit test at `stackslib/src/chainstate/nakamoto/tests/mod.rs:4118-4122`.
3. Per `GlobalStateEvaluator::reached_agreement` (`libsigner/src/v0/signer_state.rs:171-175`), a vote weight of `357` (`floor(511*7/10)`) already satisfies `reached_agreement`, even though it is below the true `358`-weight supermajority required for block approval.
4. Have exactly `357` weight worth of signers agree on a particular `active_signer_protocol_version` / global burn view / current-miner state. `determine_global_state` and `determine_global_burn_view` will report this as the network's agreed global state (`libsigner/src/v0/signer_state.rs:74-76`, `93-96`, `129-131`), even though this weight is one unit short of the 70% supermajority that the chain itself requires elsewhere for equivalent approval decisions — an off-by-one break of the intended equality between "signer-perceived agreement" and "on-chain approval threshold."

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

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4118-4122)
```rust
        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
```

**File:** libsigner/src/v0/signer_state.rs (L56-99)
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
```

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
