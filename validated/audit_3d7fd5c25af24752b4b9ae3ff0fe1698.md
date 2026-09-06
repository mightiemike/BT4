### Title
PoX-4 signer set can degenerate to `Some(vec![])`, causing Nakamoto blocks to be accepted with zero signer signatures - (File: `stackslib/src/chainstate/nakamoto/signer_set.rs`, `stackslib/src/chainstate/stacks/boot/mod.rs`, `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
The Balancer report describes `joinPool`/`exitPool` vacuously succeeding when `_tokens.length == 0`, because the loop and ratio checks degrade to no-ops. The same class of bug exists in the Nakamoto signer-weight validation path: when the PoX-4 signer set degenerates to an *empty-but-`Some`* vector, `verify_signer_signatures` computes `total_weight == 0` and `threshold == 0`, so the inequality `total_weight_signed < threshold` becomes `0 < 0`, which is false — a block with **zero signer signatures** is accepted as validly signed.

### Finding Description
`NakamotoBlockHeader::verify_signer_signatures` only rejects a reward set when `reward_set.signers()` returns `None`: [1](#0-0) 
It never checks whether the returned `Vec` is empty. The total weight is then computed as the sum over that (possibly empty) vector: [2](#0-1) 
which is `0` for an empty vector, and the threshold derived from a `total_weight` of `0` is also `0`: [3](#0-2) 
Finally, the acceptance check `total_weight_signed < threshold` becomes `0 < 0` (false), so the block is accepted: [4](#0-3) 

The `Some(vec![])` degenerate case is reachable through the PoX-4 signer-set computation. `make_signer_set` floors each signer's weight to `stacked_amt / threshold` and drops any signer whose floored weight is `0`: [5](#0-4) 
If *every* aggregated signing key floors to `0` (the exact "old floor-and-drop scheme" bug that the codebase's own regression test explicitly documents and which was fixed only for PoX-5's Hare-round allocation): [6](#0-5) 
the function still returns `Some(vec![])` (an empty-but-`Some` list), because the `Some(signer_set)` wrap happens unconditionally after filtering.

Unlike `pox_5_compute_and_update_signers`, which explicitly guards against an empty resulting signer set and aborts with `PoxNoRewardCycle`: [7](#0-6) 
`pox_4_compute_and_update_signers` has **no equivalent guard** — it writes whatever `make_signer_set` produced (including `Some(vec![])`) straight to the `.signers` contract and returns it as the `RewardSet`: [8](#0-7) 

Downstream, `read_reward_set_at_calculated_block` (the path used to load the reward set for signature verification during normal Nakamoto tenure operation) only checks `reward_set.signers().is_none()`, not emptiness, so the empty-but-`Some` list passes through unblocked: [9](#0-8) 
(The separate `OnChainRewardSetProvider::get_reward_set`, used only for the epoch-2-style path, does check `is_empty()`, but that is not the path used by `read_reward_set_at_calculated_block`, which is the one that actually seeds Nakamoto block/header validation.)

### Impact Explanation
If the PoX-4 signer set for a reward cycle degenerates to `Some(vec![])` (every registered signer's aggregated stacked amount floors to a weight of `0` under the `ceil(total/reward_slots)` threshold), then for that entire reward cycle `verify_signer_signatures` accepts **any** Nakamoto block header regardless of how many (or how few — including zero) signer signatures it carries. This breaks the core equality the signer subsystem is supposed to enforce: "a block is only valid if signers holding ≥70% of signing weight approved it." A miner could push blocks with no signer approval at all, and the check would still return success. This is a critical, network-wide "invalid block accepted" condition, since every node computes `total_weight = 0` and `threshold = 0` identically and deterministically from the same on-chain reward-set data — it is not a chain-split, but it is a systemic bypass of the block-approval consensus mechanism.

### Likelihood Explanation
This requires a specific, structural condition of the PoX-4 stacking registrations for a cycle (every distinct signing key's aggregated `amount_stacked` floors to `0` under the computed threshold) rather than any signer/miner majority. The regression test committed for PoX-5's Hare-round fix explicitly documents that this "floor-and-drop" degeneration is a real, previously-encountered failure mode of the exact algorithm still used unguarded by `pox_4_compute_and_update_signers`. It can plausibly be engineered by an attacker who controls or influences the distribution of many small/distinct signing keys relative to the reward-slot threshold for a cycle, requiring no majority of signers or miners — only participation in PoX-4 stacking, which is unprivileged.

### Recommendation
- Add the same guard used in `pox_5_compute_and_update_signers` to `pox_4_compute_and_update_signers`: if `make_signer_set` returns `Some(vec![])` (or `None`), treat it as `PoxNoRewardCycle`/burn instead of writing an empty signer set forward.
- In `verify_signer_signatures`, explicitly reject (rather than silently pass through) reward sets whose `signers()` is `Some(empty vec)`, mirroring the `None` check, e.g. `if signers.is_empty() { return Err(...) }`.
- Add the same `is_empty()` check to `read_reward_set_at_calculated_block` that already exists in `OnChainRewardSetProvider::get_reward_set`, so both reward-set loading paths reject degenerate empty signer sets consistently.

### Proof of Concept
1. During a PoX-4 reward cycle, arrange stacking registrations such that every distinct signing key's aggregated `amount_stacked` is individually less than `threshold = ceil(total_ustx_locked / reward_slots)` (e.g., many small, distinct signing keys, none large enough to clear the per-slot threshold), while `rewarded_addresses` remains non-empty.
2. `make_signer_set` (`stackslib/src/chainstate/stacks/boot/mod.rs:1020-1072`) floors every entry's weight to `0` and filters all of them out, returning `Some(vec![])`.
3. `pox_4_compute_and_update_signers` (`stackslib/src/chainstate/nakamoto/signer_set.rs:703-738`) writes this empty `Some(vec![])` signer set to `.signers` and returns it as the cycle's `RewardSet`, with no guard against emptiness.
4. `read_reward_set_at_calculated_block` (`stackslib/src/chainstate/nakamoto/coordinator/mod.rs:221-245`) loads this reward set and only checks `signers().is_none()`, which is false — it accepts the empty-but-`Some` set.
5. A miner produces a Nakamoto block header with `signer_signature: vec![]` (no signer signatures at all) for this reward cycle.
6. `verify_signer_signatures` (`stackslib/src/chainstate/nakamoto/mod.rs:1097-1190`) computes `total_weight = 0`, `threshold = compute_voting_weight_threshold(0) = 0`, and `total_weight_signed = 0` (loop over an empty `signer_signature` vector never executes); the check `0 < 0` is false, so the function returns `Ok(0)` — the block is treated as validly signed despite having zero signer approvals.

### Citations

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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L495-503)
```rust
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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1020-1072)
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
    }
```

**File:** stackslib/src/chainstate/nakamoto/tests/signer_set.rs (L294-321)
```rust
#[test]
fn equal_stakes_exceeding_reward_slots_are_not_all_zeroed() {
    // Regression: more distinct signers than reward_slots, all with equal stake.
    //
    // The old floor-and-drop scheme set threshold = ceil(N*S / R) > S, so every
    // signer's weight floored to 0 and the entire set was dropped -- stalling the
    // chain. The Hare round must instead award one slot each to the top `R` signers
    // (by remainder, then signing_key), dropping only the surplus signers.
    let pox_constants = test_pox_constants(1); // reward_slots() == 4
    let reward_slots = pox_constants.reward_slots();
    assert_eq!(reward_slots, 4);
    let stake = 1_000_000u128;
    // 5 signers, only 4 slots.
    let entries: Vec<_> = (0..5u8)
        .map(|i| RawPox5Entry {
            signer_key: signer_key(i),
            amount_ustx: stake,
        })
        .collect();
    let mut iter = entries.into_iter().map(Ok);
    let Pox5SignerSetOutput { signer_set, .. } =
        NakamotoSigners::pox_5_make_signer_set(&mut iter, &pox_constants).expect("ok");

    assert_eq!(
        signer_set.len(),
        reward_slots as usize,
        "expected exactly reward_slots signers, not an empty/zeroed set"
    );
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

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L221-245)
```rust
        if reward_set
            .rewarded_addresses()
            .map_or(false, |addrs| addrs.is_empty())
        {
            // no one is stacking (V0 with empty rewarded_addresses)
            err_or_debug!(debug_log, "No PoX participation");
            return Err(Error::PoXAnchorBlockRequired);
        }

        inf_or_debug!(
            debug_log,
            "PoX reward set loaded from written block state";
            "reward_set_block_id" => %reward_set_block.index_block_hash(),
            "burn_block_hash" => %reward_set_block.burn_header_hash,
            "stacks_block_height" => reward_set_block.stacks_block_height,
            "burn_header_height" => reward_set_block.burn_header_height,
        );

        if reward_set.signers().is_none() {
            err_or_debug!(
                debug_log,
                "FATAL: PoX reward set did not specify signer set in Nakamoto"
            );
            return Err(Error::PoXAnchorBlockRequired);
        }
```
