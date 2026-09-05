### Title
`verify_signer_signatures` accepts an unsigned Nakamoto block when the reward-cycle's computed signer set is empty - ([File: stackslib/src/chainstate/nakamoto/mod.rs])

### Summary
`NakamotoBlockHeader::verify_signer_signatures` treats "no registered signers" and "a registered-but-degenerate-empty signer list" differently: it only rejects the former (`reward_set.signers() == None`) but silently accepts the latter, because an empty-but-`Some` signer vector drives the required approval threshold to `0`, which any signature count (including zero) trivially satisfies. This mirrors the Nimiq `BlockInclusionProof::is_block_proven` flaw, where an empty proof-input list causes the verification routine to short-circuit to "true" without doing any cryptographic check.

### Finding Description
`RewardSet::signers()` returns `Some(&Vec<NakamotoSignerEntry>)` for the `Waterfall` variant unconditionally, and for the `V0` variant whenever the `signers` field is `Some(_)` — even if that vector is empty. `StacksChainState::make_signer_set` (`stackslib/src/chainstate/stacks/boot/mod.rs:1020-1072`) builds this vector by aggregating stacked amounts per signing key and then filtering out any entry whose `weight == u32::try_from(stacked_amt / threshold) == 0`: [1](#0-0) 
If every aggregated signing key's stake floors to zero slots relative to the PoX threshold (a realistic outcome when the reward-cycle threshold is scaled to 25% of liquid STX, per `get_reward_threshold_and_participation`, while stackers spread stake across many distinct signing keys each below that per-key threshold), `make_signer_set` returns `Some(vec![])` rather than `None`. This is *not* rejected anywhere in `RewardSet::empty()`-style guards, unlike the `pox_5` path (`stackslib/src/chainstate/nakamoto/signer_set.rs:763-766`), which explicitly errors out on `signer_set.is_empty()`.

`verify_signer_signatures` only guards against `None`: [2](#0-1) 
It then computes `total_weight = reward_set.total_signing_weight()`, which sums an empty vector to `0`: [3](#0-2) 
and derives the threshold as: [4](#0-3) 
With `total_weight == 0`, `compute_voting_weight_threshold` returns `0`. Back in `verify_signer_signatures`, the loop over `self.signer_signature` (which is empty because there are no valid signers to sign against) never executes, so `total_weight_signed` stays `0`: [5](#0-4) 
The final check `if total_weight_signed < threshold` evaluates `0 < 0 == false`, so the function returns `Ok(0)` — the block is treated as validly signed even though **zero** signer signatures were ever verified. This is structurally identical to the Nimiq bug: an empty collection (`get_interlink_hops` there, the signer vector here) causes the verification routine to skip all cryptographic checks and return a "proven"/"valid" verdict.

### Impact Explanation
`verify_signer_signatures` is deterministic given the on-chain-computed `RewardSet`, so every honest node reaches the same degenerate empty-signer state for that reward cycle. Any miner (no elevated privilege — merely needs to win a sortition, which any registered miner is entitled to) can then propose and get accepted a Nakamoto block header with `signer_signature: vec![]`, entirely bypassing the signer-committee approval mechanism that Nakamoto's security model depends on for block finality/reorg-resistance. This is a network-wide "invalid block accepted" condition: a block that was never actually attested to by any Nakamoto signer is treated by every node as if it met the 70%-weight signer approval threshold.

### Likelihood Explanation
Triggering requires only that the PoX-4/legacy reward-set computation (`make_signer_set`) yield an aggregated per-signing-key weight of zero for every distinct signing key — reachable without majority control, purely by a set of unprivileged stackers splitting their stacked STX across enough distinct signing keys that no single key's aggregate crosses the reward-cycle's per-slot threshold, while overall participation still remains high enough to activate PoX for that cycle. No secret key, admin action, or node-operator cooperation is needed.

### Recommendation
In `verify_signer_signatures`, explicitly reject when `signers.is_empty()` (not just when `reward_set.signers()` is `None`), returning `ChainstateError::InvalidStacksBlock`/`NoRegisteredSigners` in that case, mirroring the explicit empty-check already present in the `pox_5_compute_and_update_signers` path. Additionally, harden `RewardSet::signers()`/`total_signing_weight()` so an empty vector is normalized to `None`/an error, preventing any other caller from repeating this same "empty-but-Some" trap.

### Proof of Concept
1. Construct (or naturally reach, via PoX stacking) a reward cycle where `StacksChainState::make_signer_set` aggregates stake across many distinct signing keys such that every key's `stacked_amt / threshold == 0`.
2. Observe `RewardSet::V0` (or `Waterfall`) is persisted with `signers: Some(vec![])`.
3. During that reward cycle, have a miner produce a `NakamotoBlock` whose header has `signer_signature: vec![]`.
4. Call `header.verify_signer_signatures(&reward_set, epoch_id)`; the function returns `Ok(0)` instead of an error, and the block is accepted as validly signed by every node computing the same reward set, despite receiving zero real signer approvals.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L493-503)
```rust
    /// Return the total `weight` of all signers in the reward set.
    /// If there are no reward set signers, a ChainstateError is returned.
    pub fn total_signing_weight(&self) -> Result<u32, String> {
        let Some(signers) = self.signers() else {
            return Err("Unable to calculate total weight - No signers in reward set".to_string());
        };
        Ok(signers.iter().map(|s| s.weight).fold(0, |s, acc| {
            acc.checked_add(s)
                .expect("FATAL: Total signer weight > u32::MAX")
        }))
    }
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1051-1065)
```rust
        let mut signer_set: Vec<_> = signer_set
            .into_iter()
            .filter_map(|(signing_key, stacked_amt)| {
                let weight = u32::try_from(stacked_amt / threshold)
                    .expect("CORRUPTION: Stacker claimed > u32::max() reward slots");
                if weight == 0 {
                    return None;
                }
                Some(NakamotoSignerEntry {
                    signing_key,
                    stacked_amt,
                    weight,
                })
            })
            .collect();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1097-1107)
```rust
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };
```

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
