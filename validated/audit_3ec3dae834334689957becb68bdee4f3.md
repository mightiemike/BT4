### Title
Zero total-signer-weight reward set makes `verify_signer_signatures` trivially satisfied by zero signatures - ([File: stackslib/src/chainstate/nakamoto/mod.rs])

### Summary
`NakamotoBlockHeader::verify_signer_signatures` accepts a block once `total_weight_signed >= threshold`, where `threshold = compute_voting_weight_threshold(total_weight)`. When the reward set's total signer weight is `0`, the computed threshold is also `0`, so a block whose `signer_signature` vector is empty (`total_weight_signed = 0`) satisfies `0 < 0 == false` and is accepted as validly signed — with **zero real signatures**. This is the structural analog of the Vyper `@nonreentrant("")` bug: an "empty" input (empty signer set / empty weight) silently degrades a guard into a no-op instead of being rejected up front.

### Finding Description
`compute_voting_weight_threshold` computes the threshold purely as a function of `total_weight`: [1](#0-0) 

When `total_weight == 0`, `(total_weight * threshold) / 10 + ceil == 0`, so `compute_voting_weight_threshold(0) == 0`.

`verify_signer_signatures` uses this threshold as the acceptance gate: [2](#0-1) [3](#0-2) 

If `reward_set.total_signing_weight()` returns `0` (i.e., the signers list is `Some(vec![])`, non-`None` but empty, or every entry has weight `0`) and the block carries no `signer_signature` entries, `total_weight_signed` stays `0`, `threshold` is `0`, and `0 < 0` is `false` — the function returns `Ok(0)` instead of an error. The equality this is meant to enforce — "at least the consensus-required weight of registered signers approved this block" — is broken: the block is accepted as approved despite having no approving signers at all.

The `RewardSet` and `total_signing_weight` helpers show the distinction between "no signers" (`None`, which is rejected) and "empty signers" (`Some(vec![])`, which is *not* rejected by this path): [4](#0-3) 

The classic (V0) signer-set builder, `StacksChainState::make_signer_set`, can produce exactly this degenerate `Some(vec![])` result: it only short-circuits to `None` when the *input* `entries` slice is empty or when entries have no signing keys at all; if entries are non-empty but every entry's `stacked_amt / threshold` computes to `weight == 0`, the `filter_map` drops every entry while still returning `Some(signer_set)` with `signer_set` empty: [5](#0-4) 

Notably, the newer PoX-5 "Waterfall" signer-set computation explicitly recognizes and rejects this exact hazard by checking `signer_set.is_empty()` and erroring out with `ChainstateError::PoxNoRewardCycle`: [6](#0-5) 

I was not able to confirm, within the tool budget available, whether the classic V0 reward-set computation path that calls `make_signer_set` (in `stackslib/src/chainstate/nakamoto/signer_set.rs`) has an equivalent guard against the empty-`Some(vec![])` result before it is persisted and later consumed by `verify_signer_signatures`. This is the key open question for confirming full exploitability: if that call site also unconditionally accepts an empty `Some(vec![])` signer set (unlike the PoX-5 path, which explicitly guards it), then the trivial-pass condition described above is reachable on mainnet without requiring majority collusion — it only requires reward-cycle economics that leave every stacker's weight rounding down to zero (e.g., an extremely large number of very small stackers relative to `reward_slots`), a condition an attacker/minority participant does not need special privileges to arrange, only enough small stackers (or one attacker splitting stake thinly) to drive every entry's floor-division weight to 0.

### Impact Explanation
If reachable, this allows a Nakamoto block to be accepted network-wide as "signer-approved" while carrying **no valid signer signatures at all**, i.e. a validation verdict where honest nodes computing the same `total_weight`/`threshold` would all trivially accept an unsigned block. That corresponds to "an invalid block accepted... network-wide" — a Critical-class outcome per the scope's impact taxonomy, since it defeats the entire signer-approval security model for any tenure whose reward cycle happens to land in this degenerate all-zero-weight state.

### Likelihood Explanation
This is medium-to-uncertain likelihood: the degenerate zero-weight reward set is not attacker-forced in a single transaction; it depends on the reward-cycle's stacked-amount distribution making `stacked_amt / threshold` floor to `0` for every entry (or the "no signers" path returning an empty-but-`Some` list). The PoX-5 path is already hardened against exactly this case, indicating the developers are aware of the hazard for one branch; it is unconfirmed whether the classic/V0 path enjoys equivalent protection. Without further code access I cannot elevate this beyond a plausible, code-supported bug-class analog.

### Recommendation
Add an explicit check in `verify_signer_signatures` (or in the reward-set construction path immediately before it is written into the reward-cycle-info table) rejecting any reward set with `total_signing_weight() == 0` or `signers().is_none_or(|s| s.is_empty())`, mirroring the guard already present in `pox_5_compute_and_update_signers`. Additionally, `compute_voting_weight_threshold` should reject/short-circuit a `total_weight` of `0` rather than silently returning `0`, so an all-zero-weight signer set can never be treated as "already met."

### Proof of Concept
Conceptual (concrete reproduction requires deeper access to the reward-set-computation call site to confirm the missing guard):
1. Arrange stacked amounts across a reward cycle in classic (V0) PoX so that `threshold = total_ustx / reward_slots` is large enough that every individual stacker's `amount_stacked / threshold` computes to `0` (e.g., many stackers each just under the per-slot threshold).
2. `StacksChainState::make_signer_set` returns `Some(vec![])` (empty signer set, not `None`) because the input `entries` are non-empty and expect signing keys, but the weight filter drops every entry — see `stackslib/src/chainstate/stacks/boot/mod.rs:1020-1071`.
3. This reward set is used for the following cycle's tenures; `RewardSet::total_signing_weight()` returns `0`.
4. A miner emits a Nakamoto block for that tenure with `signer_signature = vec![]`.
5. `verify_signer_signatures` computes `threshold = compute_voting_weight_threshold(0) = 0`, `total_weight_signed = 0`, and `0 < 0` is `false`, so the block is accepted as validly signer-approved with zero real signatures — see `stackslib/src/chainstate/nakamoto/mod.rs:1115-1189`.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1115-1124)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1194-1206)
```rust
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
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L493-513)
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

    /// Return a reference to the signers list.
    /// V0 returns `None` if the signers field is `None`;
    /// Waterfall always has signers.
    pub fn signers(&self) -> Option<&Vec<NakamotoSignerEntry>> {
        match self {
            RewardSet::V0(v0) => v0.signers.as_ref(),
            RewardSet::Waterfall(wf) => Some(&wf.signers),
        }
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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L763-766)
```rust
        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
```
