### Title
Empty-but-`Some` signer set collapses the Nakamoto block-approval threshold to zero, allowing a block with zero signer signatures to be accepted - (File: stackslib/src/chainstate/nakamoto/mod.rs, stackslib/src/chainstate/stacks/boot/mod.rs)

### Summary
`NakamotoBlockHeader::verify_signer_signatures` only rejects a reward set when `reward_set.signers()` returns `None`; it does not reject a reward set whose signer list is present but empty (`Some(vec![])`). `StacksChainState::make_signer_set` can legitimately return `Some(vec![])` when stacking participation exists but every aggregated signing key's stacked amount falls below the per-slot threshold (all entries are filtered out for weight `== 0`). In that case `total_signing_weight()` returns `0`, and `NakamotoBlockHeader::compute_voting_weight_threshold(0)` also evaluates to `0`, so the check `total_weight_signed < threshold` (`0 < 0`) is false - i.e. a block with **zero** signer signatures satisfies the "sufficient signer weight" check.

### Finding Description
`verify_signer_signatures` (`stackslib/src/chainstate/nakamoto/mod.rs`) does: [1](#0-0) 
This only guards against `None` signers, not an empty `Vec`.

`RewardSet::total_signing_weight` folds over the signer list and returns `0` for an empty vector (not an error, since `signers()` is `Some`): [2](#0-1) 

`compute_voting_weight_threshold` computes `threshold = ceil(total_weight * 7 / 10)`, which is `0` when `total_weight == 0`: [3](#0-2) 

Back in `verify_signer_signatures`, with `total_weight = 0`, the signature loop over `self.signer_signature` (which can legitimately be empty) never runs, so `total_weight_signed` stays `0`, and the final check `if total_weight_signed < threshold` becomes `0 < 0 = false`, so the function returns `Ok(0)` - the block is treated as validly signed: [4](#0-3) 

The root cause is `make_signer_set` in the PoX-4 reward-set computation path. It returns `None` only when the raw entries list is empty from the start; if entries exist but every aggregated signing key's `stacked_amt / threshold == 0`, the `filter_map` drops all of them and the function still returns `Some(empty_vec)`, not `None`: [5](#0-4) 

This `Some(vec![])` reward set is written into the `.signers` boot contract by `update_signers`/`pox_4_compute_and_update_signers` whenever `participation > 0` even though the resulting `signer_set` is empty: [6](#0-5) 

Notably, the PoX-5 path (`pox_5_compute_and_update_signers`) explicitly guards against this by checking `signer_set.is_empty()` and returning `ChainstateError::PoxNoRewardCycle`: [7](#0-6) 
No equivalent guard exists on the PoX-4 (`make_signer_set`/`pox_4_compute_and_update_signers`) path, so the vulnerable `Some(empty)` reward set can be committed to chain state and consensus-critical signature verification.

This mirrors the external report's bug class exactly: an internally-uninitialized/empty collection silently short-circuits a security-critical loop/threshold check, causing the intended validation ("signer weight must exceed threshold") to be trivially satisfied instead of properly enforced.

### Impact Explanation
This is a **minority-triggerable** condition: it requires stacking participation to be split across enough distinct signing keys that every individual signing key's aggregated stacked amount is below the reward-cycle's per-slot threshold, while total participation remains `> 0` (`has_participation = true`). This is a normal outcome of the existing "missed reward slot" mechanic (stackers who stack too little relative to the number of slots are already expected to fall below the threshold); it does not require control of a majority of stake, a validator/operator key, or any privileged role - it only requires ordinary PoX-4 stacking transactions from otherwise-unprivileged accounts (potentially even a single actor splitting the same total stake across many low-value stacking transactions with distinct signer keys). Once the resulting reward set for that cycle has `signers = Some(vec![])`, **every node in the network** that computes/loads that reward set will independently compute `threshold = 0`, so this is not a fork/disagreement issue - it is a network-wide validity bypass: any block for that reward cycle, signed by nobody (`signer_signature = vec![]`), will pass `verify_signer_signatures` and be treated as validly approved by signers. This breaks the core Nakamoto consensus invariant that a block must be approved by signers holding at least the threshold voting weight, i.e. an invalid block (zero signer approval) is accepted network-wide - a Critical-severity outcome.

### Likelihood Explanation
Likelihood is high in principle because it requires no majority stake and no privileged access - only a distribution of PoX-4 stacking transactions across enough distinct signer keys such that individual per-key totals miss the per-slot threshold while aggregate participation is nonzero. This is exactly the boundary condition the codebase already anticipates and partially handles for PoX-5 (`signer_set.is_empty()` check) but not for the PoX-4/`make_signer_set` path used by `pox_4_compute_and_update_signers`. Because reaching this state depends only on economic/participation parameters of ordinary stacking, and not any privileged action, it is realistically triggerable by any set of unprivileged stackers, though it may require deliberate structuring of stacking transactions to guarantee the "all signer keys below threshold, but total participation > 0" condition for a specific reward cycle.

### Recommendation
- In `make_signer_set` (`stackslib/src/chainstate/stacks/boot/mod.rs`), after filtering out zero-weight entries, if the resulting `signer_set` is empty but the input `entries` was non-empty (i.e., `has_participation`), return `None` instead of `Some(empty_vec)`, or otherwise propagate this as "no valid signer set" so downstream code treats the cycle consistently (either treat it as `has_participation = false`, or hard-fail cycle progression like the PoX-5 path does with `ChainstateError::PoxNoRewardCycle`).
- In `verify_signer_signatures` (`stackslib/src/chainstate/nakamoto/mod.rs`), explicitly reject reward sets where `signers.is_empty()`, not only `None`, before proceeding to threshold computation.
- In `RewardSet::total_signing_weight`, treat an empty (but `Some`) signer vector the same as `None` (return an `Err`), so `total_weight = 0` can never silently occur.
- Add regression tests mirroring the PoX-5 `signer_set.is_empty()` guard for the PoX-4 path, and a test asserting that a block with `signer_signature = vec![]` is rejected whenever the associated reward cycle has zero effective signer weight.

### Proof of Concept
1. Configure PoX-4 stacking for a reward cycle such that N distinct stacker principals each choose a **distinct** signing key and stack an amount `A` where `A / threshold == 0` for every one of them (e.g., threshold computed from `liquid_ustx` and available reward slots is larger than any individual `A`), while total participation across all of them is `> 0`.
2. Run `pox_4_compute_and_update_signers` for that reward cycle: `get_reward_threshold_and_participation` reports `participation > 0`; `make_reward_set`/`make_signer_set` aggregates by signing key, and since every aggregated `stacked_amt / threshold == 0`, the `filter_map` drops all entries, yielding `signer_set = Some(vec![])`. `has_participation = participation > 0 = true`, so `update_signers` is invoked and writes this into the `.signers` boot contract for the cycle (per `stackslib/src/chainstate/nakamoto/signer_set.rs:703-738`).
3. During Nakamoto block validation for a block in that reward cycle, construct a `NakamotoBlockHeader` with `signer_signature = vec![]` (no signatures at all).
4. Call `verify_signer_signatures`: `reward_set.signers()` returns `Some(&vec![])` (passes the `None` check), `total_signing_weight()` returns `0`, `compute_voting_weight_threshold(0)` returns `0`, the empty signature loop leaves `total_weight_signed = 0`, and `0 < 0` is `false`, so the function returns `Ok(0)` - the block is accepted as validly signer-approved despite having no signer signatures whatsoever.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1097-1124)
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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1020-1071)
```rust
    pub fn make_signer_set(
        threshold: u128,
        entries: &[RawRewardSetEntry],
    ) -> Option<Vec<NakamotoSignerEntry>> {
        let Some(first_entry) = entries.first() else {
            // entries is empty: there's no signer set
            return None;
        };
        // signing keys must be all-or-nothing in the reward set
        let expects_signing_keys = first_entry.signer.is_some();
        for entry in entries.iter() {
            if entry.signer.is_some() != expects_signing_keys {
                panic!("FATAL: stacking-set contains mismatched entries with and without signing keys.");
            }
        }
        if !expects_signing_keys {
            return None;
        }

        let mut signer_set = BTreeMap::new();
        for entry in entries.iter() {
            let signing_key = entry
                .signer
                .expect("BUG: signing keys should all be set in reward-sets with any signing keys");
            if let Some(existing_entry) = signer_set.get_mut(&signing_key) {
                *existing_entry += entry.amount_stacked;
            } else {
                signer_set.insert(signing_key, entry.amount_stacked);
            };
        }

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

        // finally, we must sort the signer set: the signer participation bit vector depends
        //  on a consensus-critical ordering of the signer set.
        signer_set.sort_by_key(|entry| entry.signing_key);

        Some(signer_set)
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L703-738)
```rust
    fn pox_4_compute_and_update_signers(
        clarity: &mut ClarityTransactionConnection,
        pox_constants: &PoxConstants,
        reward_cycle: u64,
        pox_contract: &str,
        coinbase_height: u64,
    ) -> Result<SignerCalculation, ChainstateError> {
        let is_mainnet = clarity.is_mainnet();
        let signers_contract = &boot_code_id(SIGNERS_NAME, is_mainnet);

        let liquid_ustx = clarity.with_clarity_db_readonly(|db| db.get_total_liquid_ustx())?;
        let reward_slots = Self::get_pox_4_reward_slots(clarity, reward_cycle, pox_contract)?;
        let (threshold, participation) = StacksChainState::get_reward_threshold_and_participation(
            pox_constants,
            &reward_slots[..],
            liquid_ustx,
        );

        let reward_set =
            StacksChainState::make_reward_set(threshold, reward_slots, StacksEpochId::Epoch30);

        test_debug!("Reward set for cycle {}: {:?}", &reward_cycle, &reward_set);

        let empty_signers = vec![];
        let events = Self::update_signers(
            clarity,
            reward_cycle,
            reward_set.signers().unwrap_or(&empty_signers),
            signers_contract,
            participation > 0,
            coinbase_height,
            is_mainnet,
        )?;

        Ok(SignerCalculation { events, reward_set })
    }
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L763-766)
```rust
        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
```
