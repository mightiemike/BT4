Based on my research, I found a concrete analog of the "zero liquidity, trivial equality" bug class in the Nakamoto signer-weight threshold logic.

### Title
Zero total signing weight makes the 70% signature threshold trivially satisfiable by zero signatures - (File: stackslib/src/chainstate/nakamoto/mod.rs)

### Summary
`NakamotoBlockHeader::verify_signer_signatures` accepts a block as validly signed whenever `total_weight_signed >= threshold`, where `threshold` is computed by `compute_voting_weight_threshold(total_weight)`. If the reward set's `total_weight` is `0`, the threshold computation collapses to `0`, so a block with **zero signer signatures** (`total_weight_signed == 0`) passes the `0 < 0` check and is treated as validly signed, exactly mirroring the Uniswap V3 bug where zero liquidity makes `amountRemainingLessFee == amountIn == 0`, trivially satisfying an equality/inequality that should gate state transitions.

### Finding Description
`compute_voting_weight_threshold` computes the ceiling of `total_weight * 7 / 10`: [1](#0-0) 

When `total_weight == 0`, `(0 * 7) % 10 == 0`, so `ceil = 0`, and the function returns `0`.

In `verify_signer_signatures`, `total_weight` is derived from the reward set's `total_signing_weight()`, and the function only rejects the reward set if `signers()` returns `None` (not if the signer list is empty with weight zero): [2](#0-1) 

The signature loop accumulates `total_weight_signed` only from `self.signer_signature`. If that vector is empty (no signatures at all), `total_weight_signed` stays `0`. The final acceptance check is: [3](#0-2) 

With `threshold == 0` and `total_weight_signed == 0`, the condition `total_weight_signed < threshold` (`0 < 0`) is false, so the function falls through to `Ok(total_weight_signed)` — i.e., `Ok(0)` — instead of erroring out. This is structurally identical to the Uniswap defect: a "zero-liquidity" state (`total_weight == 0`) degenerates the guard into a no-op, letting the caller satisfy an equality (`signed >= threshold`) that was meant to enforce majority consensus, with zero actual input (zero signatures) required.

### Impact Explanation
If this code path is reachable with `total_weight == 0` (e.g., a reward set whose signer list is present but sums to zero weight), a Nakamoto block could be accepted by chainstate validation as having a sufficient signer quorum despite carrying **no signer signatures at all**, breaking the fundamental equality that "block acceptance requires signer-weight majority." This would let a single unprivileged block proposer push through an unsigned block, causing nodes that reach this state to accept a block the rest of the network (with correctly populated reward sets) would reject — a state-root/validation divergence and potential chain split, matching the "Critical: invalid block accepted network-wide" / "High: minority-triggerable validation divergence" categories.

### Likelihood Explanation
This is **low-to-uncertain likelihood** given available context: the practical exploitability hinges entirely on whether a `RewardSet` can legitimately reach `total_signing_weight() == 0` while `signers()` is `Some(vec![])` or `Some(vec![... all-zero-weight entries...])` rather than `None`. I could not fully verify the construction/validation guarantees of `RewardSet` and `total_signing_weight()` (defined in `stackslib/src/chainstate/stacks/boot/mod.rs`) within the remaining search budget, so I cannot confirm this zero-weight state is reachable through normal PoX/reward-set-selection logic. If reward-set construction guarantees at least one signer with nonzero weight whenever `signers()` is populated, this specific path is unreachable and the impact is theoretical only.

### Recommendation
In `compute_voting_weight_threshold`, explicitly reject `total_weight == 0` (return an error such as `ChainstateError::NoRegisteredSigners`) rather than silently computing a threshold of `0`. Additionally, in `verify_signer_signatures`, treat an empty `signer_signature` vector or a `total_weight == 0` reward set as an immediate hard error, independent of the `total_weight_signed < threshold` comparison, so that "zero signers/zero weight" can never be interpreted as "quorum reached."

### Proof of Concept
Conceptually (matching the Uniswap PoC structure): construct a `RewardSet` whose `signers` field is `Some(vec![])` (empty vector, not `None`) or `Some(vec![NakamotoSignerEntry{weight: 0, ...}])`, so `total_signing_weight() == 0`. Call `compute_voting_weight_threshold(0)` → returns `Ok(0)`. Then call `verify_signer_signatures` on a `NakamotoBlockHeader` with `signer_signature: vec![]` — the function returns `Ok(0)` instead of an error, because `total_weight_signed (0) < threshold (0)` is false. [4](#0-3) [3](#0-2)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1097-1131)
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

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();
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
