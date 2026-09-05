### Title
Floor-division rounds down the 70% signer-agreement threshold in `GlobalStateEvaluator`, letting less-than-supermajority weight force miner/protocol-version/burn-view consensus — (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` compute the Nakamoto signer supermajority threshold with **floor** integer division instead of the **ceiling** division used everywhere else in the codebase for the same constant (`NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD = 7`). This is the exact bug class from the referenced report: an integer-division threshold that silently rounds down for certain totals, letting a smaller share of weight satisfy a nominally fixed percentage requirement.

### Finding Description
`libsigner/src/v0/signer_state.rs`:

```rust
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
``` [1](#0-0) 

`total_weight * 7 / 10` truncates towards zero. For any `total_weight` that is not a multiple of 10, this yields a required weight strictly *less* than the true 70% (e.g. `total_weight = 13` → floor(9.1) = 9, and 9/13 ≈ 69.2% < 70%; `total_weight = 4` → floor(2.8) = 2, and 2/4 = 50% < 70%). This means agreement can be "reached" by a minority that never actually holds 70% of signer weight.

By contrast, the consensus-critical block-approval threshold used for actual Nakamoto block signature verification correctly uses ceiling division and is explicitly documented and tested for round-up behavior:

```rust
pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
    let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
    let total_weight = u64::from(total_weight);
    let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
    u32::try_from((total_weight * threshold) / 10 + ceil)...
``` [2](#0-1) 

`GlobalStateEvaluator::reached_agreement` is not test-scaffolding; it drives the signer's protocol-version consensus (`determine_latest_supported_signer_protocol_version`), burn-view consensus (`determine_global_burn_view`), and current-miner/state-machine consensus (`determine_global_state`), all of which feed `LocalStateMachine::capitulate_miner_view` and `update_protocol_version` in `stacks-signer/src/v0/signer_state.rs`, which decide which miner a signer treats as the currently valid tenure. [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
Because the floor-rounded threshold is smaller than the true 70% supermajority for many non-multiple-of-10 total-weight configurations, a set of signers holding less than the intended supermajority can cause other signers to treat a protocol-version bump, burn-block view, or miner viewpoint as globally agreed and capitulate to it. Since the actual Nakamoto block-header signature check (`verify_signer_signatures` / `compute_voting_weight_threshold`) still requires the correctly-ceiled 70%, this creates a divergence between what the signer set *believes* is agreed (via the miscomputed evaluator) and what is actually required to approve blocks — i.e., a **temporary tip/viewpoint disagreement** among signers, potentially causing some signers to prematurely follow/capitulate to a miner or burn view that the rest of the network has not actually reached supermajority on.

### Likelihood Explanation
This requires no privileged access and is triggered purely by weight distributions that arise naturally from PoX-5 signer-set stake apportionment (`total_weight` is rarely a clean multiple of 10 — see `pox_5_make_signer_set`/`make_signer_set`, which assign weights based on stacked-amount ratios). Any reward cycle whose total weight isn't a multiple of 10 exhibits this discrepancy, which is the common case, making the divergence easily and unprivileged-ly reachable simply by observing normal signer-set weight totals.

### Recommendation
Make `reached_agreement`/`reached_disagreement` in `libsigner/src/v0/signer_state.rs` use the same ceiling-division approach as `NakamotoBlockHeader::compute_voting_weight_threshold`, i.e. compute the required threshold via `div_ceil` (or the existing `+9/10` idiom used elsewhere) instead of floor division, so the signer-level agreement threshold always matches the intended 70%/30% cutoffs used for actual block approval.

### Proof of Concept
1. Configure a reward set with `total_weight = 13` (e.g. via `pox_5_make_signer_set` stake distribution producing 13 total weight slots).
2. `GlobalStateEvaluator::reached_agreement(9)` returns `true` because `13 * 7 / 10 = 9` (floor), even though `9/13 ≈ 69.2% < 70%`.
3. Nine weight-units of signers agreeing on a burn view / protocol version / current-miner state causes other signers' `determine_global_state`/`capitulate_miner_view` to treat this as the finalized global state, while the actual Nakamoto block approval threshold (`compute_voting_weight_threshold(13) = 10`) still requires 10 weight-units — a strictly higher bar — producing inconsistent agreement views across the signer set.

### Citations

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

**File:** stacks-signer/src/v0/signer_state.rs (L798-838)
```rust
    fn update_protocol_version(
        &mut self,
        stacks_client: &StacksClient,
        eval: &mut GlobalStateEvaluator,
        local_supported_signer_protocol_version: u64,
    ) {
        // Before we ever access eval...we should make sure to include our own local state machine update message in the evaluation
        let Ok(local_update) =
            self.try_into_update_message_with_version(local_supported_signer_protocol_version)
        else {
            return;
        };

        let old_protocol_version = local_update.active_signer_protocol_version;
        eval.insert_update(
            stacks_client.get_signer_address().clone(),
            local_update.clone(),
        );
        // Check if we should update our active protocol version
        let active_signer_protocol_version = eval
            .determine_latest_supported_signer_protocol_version()
            .unwrap_or(old_protocol_version);

        if active_signer_protocol_version != old_protocol_version {
            info!("Signer State: Updating active signer protocol version from {old_protocol_version} to {active_signer_protocol_version}");
            crate::monitoring::actions::increment_signer_agreement_state_change_reason(
                crate::monitoring::SignerAgreementStateChangeReason::ProtocolUpgrade,
            );
            let (burn_block, burn_block_height) = local_update.content.burn_block_view();
            let current_miner = local_update.content.current_miner();
            let tx_replay_set = local_update.content.tx_replay_set();

            *self = Self::Initialized(SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: current_miner.clone().into(),
                active_signer_protocol_version,
                tx_replay_set,
            });
        }
    }
```

**File:** stacks-signer/src/v0/signer_state.rs (L928-963)
```rust
        // Is there a miner view to which we should capitulate?
        let Some(new_miner) = self.capitulate_miner_view(
            stacks_client,
            eval,
            signerdb,
            &local_update,
            tenure_last_block_proposal_timeout,
        ) else {
            return;
        };

        let (burn_block, burn_block_height) = local_update.content.burn_block_view();
        let current_miner = local_update.content.current_miner();
        let tx_replay_set = local_update.content.tx_replay_set();

        if current_miner != &new_miner {
            info!("Signer State: Capitulating local state machine's current miner viewpoint";
                "current_miner" => ?current_miner,
                "new_miner" => ?new_miner,
                "burn_block" => %burn_block,
                "burn_block_height" => burn_block_height,
                "tx_replay_set" => ?tx_replay_set,
            );
            crate::monitoring::actions::increment_signer_agreement_state_change_reason(
                crate::monitoring::SignerAgreementStateChangeReason::MinerViewUpdate,
            );
            Self::monitor_miner_parent_tenure_update(current_miner, &new_miner);

            *self = Self::Initialized(SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: new_miner.clone().into(),
                active_signer_protocol_version: local_update.active_signer_protocol_version,
                tx_replay_set,
            });

```
