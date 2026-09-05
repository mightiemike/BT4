### Title
Empty-weight PoX-4 signer set collapses the signature threshold to zero, letting unsigned Nakamoto blocks pass validation - (File: stackslib/src/chainstate/nakamoto/signer_set.rs, stackslib/src/chainstate/nakamoto/mod.rs)

### Summary
This is the storage/initialization-collision bug class from the report (a caller ending up with default/zero state instead of the intended values) manifesting as an equality collapse in Nakamoto's signer-weight threshold check. `StacksChainState::make_signer_set` can legitimately return `Some(vec![])` (an empty-but-`Some` signer list) instead of `None` when every candidate signer's computed weight floors to zero. `pox_4_compute_and_update_signers` passes this reward set through with no emptiness check, unlike the PoX-5 path which explicitly guards against it. Downstream, `NakamotoBlockHeader::verify_signer_signatures` computes `total_weight = 0` and `threshold = compute_voting_weight_threshold(0) = 0`, so the check `total_weight_signed < threshold` becomes `0 < 0`, which is false — a block with zero signer signatures is accepted as validly signed.

### Finding Description
`RewardSet::signers()` returns `Some(&Vec<NakamotoSignerEntry>)` for both empty and non-empty vectors [1](#0-0) . `StacksChainState::make_signer_set` filters out any entry whose `weight == 0` via `filter_map`, and only returns `None` when the *input* `entries` slice is empty (i.e., `entries.first()` is `None`) [2](#0-1) . If `entries` is non-empty but every stacker's `stacked_amt / threshold` rounds down to zero (small stacks relative to the PoX threshold), the function returns `Some(vec![])` — a `Some` variant wrapping an *empty* vector, not `None`.

`pox_4_compute_and_update_signers` takes this reward set as-is and forwards `reward_set.signers().unwrap_or(&empty_signers)` into `update_signers` with `has_participation = participation > 0`, with **no check that the resulting signer list is non-empty** [3](#0-2) . Contrast this with the newer PoX-5 path, `pox_5_compute_and_update_signers`, which explicitly guards: `if signer_set.is_empty() { error!(...); return Err(ChainstateError::PoxNoRewardCycle); }` [4](#0-3)  — confirming that an empty signer set reaching consensus code is a recognized danger that was fixed in one path but not the other.

Once this `RewardSet` (with `signers = Some(vec![])`) is committed for the reward cycle, `RewardSet::total_signing_weight()` sums an empty list and returns `Ok(0)` [5](#0-4) . In `NakamotoBlockHeader::verify_signer_signatures`, `reward_set.signers()` is `Some(&[])` (not `None`), so the early "No signers in the reward set" error path is *not* taken [6](#0-5) . With zero signatures on the block, `total_weight_signed` stays `0`. `threshold = compute_voting_weight_threshold(0)` also evaluates to `0` (`(0 * 7) / 10 + 0 = 0`) [7](#0-6) . The final check `if total_weight_signed < threshold { ... }` is `0 < 0`, which is `false`, so the function returns `Ok(0)` — the block is treated as validly signed by "100% of signing weight" even though zero actual signers vouched for it [8](#0-7) .

This is the direct analog of the reported bug class: a caller (`verify_signer_signatures`, the "proxy") ends up operating on an implicitly-zeroed/uninitialized security parameter (a signer set that exists in name but carries no real weight) because the producer of that state (`pox_4_compute_and_update_signers`) never enforced the invariant that a non-trivial signer set must be committed, unlike its sibling code path that does.

### Impact Explanation
If a reward cycle's PoX-4 stacking distribution results in every signer's floor-division weight being zero (e.g., many stackers each contributing far less than the per-slot threshold, or a governance/threshold misconfiguration), the resulting `.signers` reward set for that cycle carries `total_signing_weight() == 0`. For the duration of that reward cycle, any miner can produce Nakamoto blocks with an empty `signer_signature` vector and have them accepted as fully valid by `verify_signer_signatures` on every node, since the threshold check trivially passes at `0 < 0`. This is a critical, network-wide validation defect: it allows an invalid (unsigned) block to be universally accepted, undermining the entire signer-based block approval mechanism for that cycle and enabling chain forks/duplicate tenures that no signer actually approved.

### Likelihood Explanation
Triggering this requires no privileged access and no majority coordination — it only requires that during a PoX-4 reward cycle, the stacked amounts are distributed such that every signer's weight computation floors to zero (a function of the number/size of stackers versus the threshold, both of which unprivileged stackers can influence by choosing small stack amounts, particularly plausible on testnets or low-participation networks/early cycles). The PoX-5 path already anticipated and guarded exactly this case, indicating that reaching an empty/zero-weight signer set is a realistic condition that the PoX-4 code path fails to defend against.

### Recommendation
Add the same emptiness/zero-weight guard used in `pox_5_compute_and_update_signers` to `pox_4_compute_and_update_signers`: if `make_signer_set` yields `Some(vec![])` (or the computed `total_signing_weight()` is zero) while participation is nonzero, treat this as a fatal/error condition rather than committing a nominally-non-`None` but functionally-empty signer set. Additionally, harden `verify_signer_signatures` and `compute_voting_weight_threshold` defensively: explicitly reject blocks when `total_weight == 0` (i.e., treat an empty-but-`Some` signer list the same as `None`), rather than allowing the `0 < 0` comparison to silently succeed.

### Proof of Concept
1. During a PoX-4 reward cycle, arrange for stacking participation such that `liquid_ustx`/threshold computation yields many small stackers whose `stacked_amt / threshold` all floor to `0` in `StacksChainState::make_signer_set` (e.g., threshold set high relative to typical individual stacked amounts), so `signer_set` becomes `Some(vec![])` rather than `None`.
2. Let `pox_4_compute_and_update_signers` commit this reward set (no check blocks it), so `.signers` reward-cycle state records zero real signers with `total_signing_weight() == 0`.
3. As any miner, produce a Nakamoto block for that reward cycle with `signer_signature = vec![]`.
4. Call `NakamotoBlockHeader::verify_signer_signatures(&reward_set, epoch_id)`: `signers()` returns `Some(&[])` so the "No signers" error is skipped; `total_weight_signed = 0`; `threshold = compute_voting_weight_threshold(0) = 0`; the check `0 < 0` is false, so the function returns `Ok(0)` — the unsigned block is accepted as validly approved by every node running this check.

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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L505-513)
```rust
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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1020-1065)
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1102-1107)
```rust
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
