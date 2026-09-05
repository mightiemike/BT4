## No vulnerability found for this question.

**Reasoning:** `get_signers_weights` (stackslib/src/chainstate/nakamoto/signer_set.rs:1076-1145) is a standalone read-only helper that is not called anywhere else in the codebase — a full-repo `grep_search` for `get_signers_weights` returns only its own definition, with zero callers. [1](#0-0) 

Block-validation and signer-side threshold logic instead flow through `RewardSet::signers()` / `RewardSet::total_signing_weight()` and `NakamotoBlockHeader::verify_signer_signatures`, which build their weight map from `NakamotoSignerEntry` items already stored per-signer in the `RewardSet` (a `Vec<NakamotoSignerEntry>`, not derived by re-reading `.signers` via `get_signers_weights`), and simply fold/sum weights rather than doing an address-keyed overwrite. [2](#0-1) [3](#0-2) 

Since the equality the question posits — "sum of weights in the returned HashMap == sum of weights actually committed on-chain" — is never relied upon by any consensus-critical code path (no caller uses `get_signers_weights`'s output to compute a threshold that block validation or signer coordination depends on), the claimed impact (tip disagreement, inconsistent threshold vs. `verify_signer_signatures`) cannot occur: there is no reachable path from the attacker's stacking transaction through `get_signers_weights` into block acceptance/rejection or signer voting logic. `verify_signer_signatures` uses its own independently-constructed `signers_by_pk` map and `total_signing_weight()`, which sums (not overwrites) weights and is populated from Clarity signer-set computation logic other than this function. [4](#0-3) 

Additionally, whether the `.signers` boot contract's `get-signers` Clarity function can even emit two tuples for the same `signer` principal was not verifiable from the available index content for `signers.clar`/`pox-5.clar` (the boot contract construction logic that produces the reward-cycle signer list was not fully inspectable), so the precondition itself (duplicate-address entries reaching this function) is unconfirmed. Given the confirmed absence of any caller/consumer of `get_signers_weights`'s result in consensus, sortition, or signer-threshold decisions, this does not meet the required "two honest nodes disagree" or "reward paid twice/wrong" impact bar, and is out of scope as a theoretical/dead-code finding rather than a reachable consensus divergence.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L1076-1081)
```rust
    pub fn get_signers_weights(
        chainstate: &mut StacksChainState,
        sortdb: &SortitionDB,
        block_id: &StacksBlockId,
        reward_cycle: u64,
    ) -> Result<HashMap<StacksAddress, u64>, ChainstateError> {
```

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
