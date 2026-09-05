### Title
Signer global-state agreement threshold uses floor rounding while the on-chain block-approval threshold uses ceiling rounding, letting a minority of signer weight satisfy "consensus" that would not satisfy actual block signing - (File: libsigner/src/v0/signer_state.rs)

### Summary
The stacks-signer's `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` and the chainstate's `NakamotoBlockHeader::compute_voting_weight_threshold` both intend to express the same 70%-of-weight (`NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD == 7`) supermajority rule, but they round the fractional threshold in opposite directions. This is the same class of bug as the reported PegOracle issue: a single shared constant is combined with different scaling/rounding math in two call sites, and the two computed "thresholds" silently diverge for the same input whenever `total_weight * 7` is not a multiple of 10.

### Finding Description
`NakamotoBlockHeader::compute_voting_weight_threshold` (the function that determines whether a Nakamoto block actually has enough signer weight to be accepted by the chain) rounds **up**: [1](#0-0) 

```
let ceil = if (total_weight * threshold) % 10 == 0 { 0 } else { 1 };
u32::try_from((total_weight * threshold) / 10 + ceil)
```

`GlobalStateEvaluator::reached_agreement`/`reached_disagreement` (used by the signer set to decide whether it has reached agreement on the active protocol version, the global burn view, the current miner, and the tx-replay set) rounds **down** (plain integer division, no ceiling term): [2](#0-1) 

Both formulas use the exact same constant, `NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD`, so they are meant to represent the identical fraction (70%). For any `total_weight` where `total_weight * 7 % 10 != 0`, the two thresholds differ by exactly one weight unit: `compute_voting_weight_threshold` requires `floor(total*7/10)+1`, while `reached_agreement` is satisfied at `floor(total*7/10)`.

`reached_agreement` is used throughout `GlobalStateEvaluator` to determine the network's global state view: [3](#0-2) [4](#0-3) 

That global state (protocol version, burn view, current miner, tx replay set) feeds directly into tenure-validity/timeout logic used to decide whether a miner's tenure should be treated as valid or timed out: [5](#0-4) 

So a coalition holding exactly `floor(total_weight*7/10)` weight — strictly less than the weight `compute_voting_weight_threshold` requires to actually get a Nakamoto block signed and accepted on-chain — is sufficient to make `GlobalStateEvaluator` declare "agreement reached" on a given global-state value (e.g., a particular miner or burn view). This is precisely the "signer weight below threshold" analog called out in the rules: the equality "signer weight ≥ required-approval-threshold" is broken between the two code paths that are supposed to enforce the same 70% rule.

### Impact Explanation
This is a minority-triggerable divergence between the signer set's internally-declared "global agreement" and the weight actually required by chainstate to approve/accept a Nakamoto block signature set. A signer subset whose combined weight sits in the one-unit gap between the floor and ceiling thresholds can cause the signer network to believe consensus on a miner/tenure/global-state value has been reached and act on it (e.g., treating a tenure as valid/current, switching miners) while that same weight is provably insufficient to satisfy `verify_signer_signatures`'s `compute_voting_weight_threshold` check for actually finalizing a block. This falls in the "minority-triggerable ... static-validation divergence / temporary tip disagreement" High-impact bucket, since it can desynchronize the signer set's notion of the current miner/tenure from what the chain can actually finalize.

### Likelihood Explanation
The condition is purely arithmetic and depends only on `total_weight mod 10` relative to the threshold numerator (7), which is a function of the reward-set weight distribution — not an adversarial secret. Any reward cycle whose total signer weight is not a multiple of 10 (the overwhelming majority of possible weight distributions) exhibits this one-unit gap, so the divergence is reachable without any privileged access, simply by a signer subset controlling weight in that gap window.

### Recommendation
Use identical rounding semantics for both thresholds. Either make `GlobalStateEvaluator::reached_agreement`/`reached_disagreement` use the same ceiling-rounded computation as `NakamotoBlockHeader::compute_voting_weight_threshold` (ideally by sharing one threshold-computation helper between `libsigner` and `stackslib`), or explicitly document and justify why the signer-side "soft" agreement threshold is intentionally allowed to be looser than the on-chain approval threshold, and audit all call sites (`determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, `determine_global_state`, tenure timeout logic) to confirm no chain-affecting decision is made based on the weaker threshold.

### Proof of Concept
For `total_weight = 11` (a value reachable by many reward-set weight distributions):
- `NakamotoBlockHeader::compute_voting_weight_threshold(11)` = `(11*7)/10 + 1` = `7 + 1` = `8` (ceiling, since `77 % 10 != 0`).
- `GlobalStateEvaluator::reached_agreement(7)` = `7 >= (11*7)/10` = `7 >= 7` = `true` (floor).

A signer coalition with weight `7` (out of `11`) is judged by `GlobalStateEvaluator` to have "reached agreement," while `compute_voting_weight_threshold` would require weight `8` for the corresponding block/header to actually be accepted by chainstate — demonstrating the divergence directly from the two functions' source: [6](#0-5)  versus [7](#0-6) .

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

**File:** libsigner/src/v0/signer_state.rs (L56-79)
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

**File:** stacks-signer/src/chainstate/mod.rs (L616-639)
```rust
    /// Check if the tenure identified by the ConsensusHash is timed out
    pub fn is_timed_out(
        version: &SortitionStateVersion,
        consensus_hash: &ConsensusHash,
        signer_db: &SignerDb,
        local_address: &StacksAddress,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<bool, SignerChainstateError> {
        match version {
            SortitionStateVersion::V1 => SortitionStateV1::is_timed_out(
                consensus_hash,
                signer_db,
                proposal_config.block_proposal_timeout,
            ),
            SortitionStateVersion::V2 => SortitionStateV2::is_timed_out(
                consensus_hash,
                signer_db,
                eval,
                local_address,
                proposal_config.block_proposal_timeout,
            ),
        }
    }
```
