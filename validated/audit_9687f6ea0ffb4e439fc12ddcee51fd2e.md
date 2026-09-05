### Title
Empty-but-non-`None` signer set collapses the 70% approval threshold to zero, letting a Nakamoto block with zero signatures pass `verify_signer_signatures` - (File: stackslib/src/chainstate/nakamoto/mod.rs)

### Summary
`NakamotoBlockHeader::verify_signer_signatures` only rejects a reward set when `reward_set.signers()` is `None`; it does not check whether the signer *list* is present but empty. `StacksChainState::make_signer_set` (the PoX‑4 signer-set builder) can legitimately return `Some(vec![])` — a non‑`None`, zero-length signer list — whenever every stacker's `stacked_amt / threshold` floors to `0`. In that case `total_signing_weight()` returns `Ok(0)`, `compute_voting_weight_threshold(0)` returns `0`, and a block with an **empty** `signer_signature` vector satisfies `0 < 0 == false`, so the block is accepted as validly signed with zero real signer approval.

### Finding Description
`RewardSet::signers()` distinguishes "no signer field" (`None`) from "signer field present but empty" (`Some(vec![])`) — see [1](#0-0) . `verify_signer_signatures` only guards against the former: [2](#0-1) 

If `signers` is `Some(vec![])`, `total_signing_weight()` folds over an empty iterator and returns `Ok(0)` (no error, because the `None`-check already passed) — see [3](#0-2) . Then `compute_voting_weight_threshold(0)` computes `0*7/10 = 0` with no ceiling adjustment, yielding a required threshold of `0`: [4](#0-3) 

With zero threshold, a block whose `signer_signature` vector is empty produces `total_weight_signed = 0`, and the check `total_weight_signed < threshold` (`0 < 0`) is `false`, so the function returns `Ok(0)` — signature verification succeeds with **no signatures at all**.

This function directly gates block admission in `NakamotoChainState::accept_block`, with no additional minimum-weight or minimum-signer-count check: [5](#0-4) 

The degenerate `Some(vec![])` signer set is reachable through `StacksChainState::make_signer_set`, used by the PoX‑4 path (`pox_4_compute_and_update_signers`). Unlike entries.is_empty() (which returns `None` and is handled correctly), `make_signer_set` filters out any signer whose `weight == 0` after the floor division, and if **all** signers floor to zero it returns `Some(vec![])` rather than `None`: [6](#0-5) 

This is precisely the "floor-and-drop" defect that the codebase's own comments identify and that was deliberately fixed for PoX‑5 by adding a largest-remainder ("Hare") correction so that `reward_slots` are never fully dropped: [7](#0-6) [8](#0-7) 

The PoX‑5 path additionally added an explicit guard rejecting an empty signer set outright: [9](#0-8) 

No equivalent guard exists on the PoX‑4 `make_signer_set`/`make_reward_set` path, and `pox_4_compute_and_update_signers` happily stores whatever `make_reward_set` returns: [10](#0-9) 

The threshold used by `make_signer_set`/`make_reward_set` is `get_reward_threshold_and_participation`, which derives the per-slot threshold from *aggregate* participation divided by `reward_slots`, not from any individual stacker's amount: [11](#0-10) 

Because the threshold is an aggregate-derived average, it is straightforward for a fragmented distribution of many stackers, each individually below the average threshold (even though their sum comfortably exceeds `threshold * reward_slots`), to cause every individual entry's `stacked_amt / threshold` to floor to `0`. This directly mirrors the GovHub analog: the denominator (signer weight / member count) collapsing to zero silently defeats the intended supermajority-approval invariant — except here the effect is the opposite polarity of the original report (instead of every proposal being defeated, every proposal/block is trivially "approved" with zero real approvals).

### Impact Explanation
If this reward set becomes the active signer set for a Nakamoto reward cycle, a block with **zero signer signatures** satisfies `verify_signer_signatures` and is accepted by `accept_block` into the staging block DB, and the same logic is used identically by every node (`check_nakamoto_block_signer_signature` in the p2p unsolicited-block-checking path, and the tenure downloaders), so this would be deterministically reproduced network-wide — not merely a local inconsistency. This breaks the core Nakamoto consensus invariant that a block must be approved by ≥70% of signer weight; an unsigned/miner-only block would be treated as validly signed, i.e. an invalid block is accepted network-wide. This is a Critical-class defect per the given impact taxonomy ("an invalid block accepted... network-wide").

### Likelihood Explanation
Reaching the degenerate state requires the PoX‑4 (or pre‑PoX‑5) reward-set computation to produce a scenario where every stacker's individually stacked amount floors to zero slots under the aggregate threshold, which can occur without any privileged action or majority collusion — it's a function of the natural distribution of stacking amounts relative to `reward_slots` (e.g., many small, roughly-equal stackers whose individual `stacked_amt` sits below `participation/reward_slots` even though total participation clears the network's minimum-participation bar). Given that mainnet is presently on `POX_4_NAME`-era logic per `get_reward_set_epoch2`, this path remains live. I could not fully verify from the index whether other upstream guards (e.g., minimum-stacking-amount rules elsewhere in PoX-4 that raise per-stacker minimums) reliably prevent this specific "all-floor-to-zero" configuration in practice on current mainnet parameters — that would require deeper analysis of `POX_THRESHOLD_STEPS_USTX`/`POX_MAXIMAL_SCALING` interactions and live participation numbers, which is out of scope for this static index-based review.

### Recommendation
- In `verify_signer_signatures`, explicitly reject when `signers.is_empty()` (in addition to the existing `None` check), returning `ChainstateError::InvalidStacksBlock`/`NoRegisteredSigners`.
- Alternatively/additionally, apply the same Hare/largest-remainder allocation (or an equivalent "never drop to empty" guarantee) used in `pox_5_make_signer_set` to the PoX‑4 `make_signer_set`, and add an explicit `if signer_set.is_empty() { return Err(...) }` guard in `pox_4_compute_and_update_signers`, mirroring the PoX‑5 fix at [9](#0-8) .
- Add a regression test asserting that `verify_signer_signatures` returns an error (not `Ok(0)`) for a reward set with `signers: Some(vec![])`.

### Proof of Concept
1. Construct `RawRewardSetEntry` inputs such that, for the computed `threshold = get_reward_threshold_and_participation(...).0`, every individual entry's `amount_stacked / threshold == 0` while the sum of all entries is large enough to pass `enough_participation`/count as legitimate stacking (e.g., `reward_slots = N`, and `N+1` stackers each stacking `participation/(N+1) < participation/N = threshold`).
2. Call `StacksChainState::make_reward_set(threshold, entries, epoch_id)`; observe `reward_set.signers() == Some(vec![])` (non-`None`, per `make_signer_set`'s `filter_map` on `weight == 0`), matching the pattern demonstrated by `stackslib/src/chainstate/stacks/tests/reward_set.rs` test vectors for "not enough participation for any slots to be claimed" [12](#0-11) .
3. Build a `NakamotoBlockHeader::empty()` with `signer_signature = vec![]` (no signatures at all), analogous to the existing test harness in `stackslib/src/chainstate/nakamoto/tests/mod.rs` (`test_insufficient_signatures`, `make_reward_set` helper) [13](#0-12) .
4. Call `header.verify_signer_signatures(&reward_set, StacksEpochId::latest())`; the unfixed code returns `Ok(0)` instead of an error, confirming a zero-signature block is treated as validly signed.

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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1211-1243)
```rust
    pub fn get_reward_threshold_and_participation(
        pox_settings: &PoxConstants,
        addresses: &[RawRewardSetEntry],
        liquid_ustx: u128,
    ) -> (u128, u128) {
        let participation = addresses
            .iter()
            .fold(0, |agg, entry| agg + entry.amount_stacked);

        assert!(
            participation <= liquid_ustx,
            "CORRUPTION: More stacking participation than liquid STX"
        );

        // set the lower limit on reward scaling at 25% of liquid_ustx
        //   (i.e., liquid_ustx / POX_MAXIMAL_SCALING)
        let scale_by = cmp::max(participation, liquid_ustx / POX_MAXIMAL_SCALING);

        let reward_slots = u128::try_from(pox_settings.reward_slots())
            .expect("FATAL: unreachable: more than 2^128 reward slots");
        let threshold_precise = scale_by / reward_slots;
        // compute the threshold as nearest 10k > threshold_precise
        let ceil_amount = match threshold_precise % POX_THRESHOLD_STEPS_USTX {
            0 => 0,
            remainder => POX_THRESHOLD_STEPS_USTX - remainder,
        };
        let threshold = threshold_precise + ceil_amount;
        info!(
            "PoX participation threshold is {}, from {} + {} ({}), participation is {}",
            threshold, threshold_precise, ceil_amount, scale_by, participation
        );
        (threshold, participation)
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1101-1124)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2917-2925)
```rust
        let signing_weight = block
            .header
            .verify_signer_signatures(reward_set, epoch_id)
            .inspect_err(|e| {
                warn!("Received block, but the signer signatures are invalid";
                    "block_id" => %block_id,
                    "error" => ?e,
                );
            })?;
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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L757-766)
```rust
        let mut entries = Self::pox_5_stake_entries(clarity, reward_cycle, pox_contract)?;
        let Pox5SignerSetOutput {
            signer_set,
            pox_ustx_threshold,
        } = Self::pox_5_make_signer_set(&mut entries, pox_constants)?;

        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L858-891)
```rust
        // Allocate `reward_slots` weight across signers in proportion to stake using the
        // a largest-remainder method:
        //
        // The threshold is `ceil(total / reward_slots)`.
        //
        // Flooring each signer's `stacked / threshold` assigns a base weight where the sum is `<= reward_slots`
        // (the ceil makes `total/threshold <= reward_slots`).
        //
        // This leaves some unassigned ("leftover") slots, which are handed out one-per-signer
        //  in descending fractional-remainder order (ties broken by pubkey-sort order).
        //
        // This avoids degenerate modes of the floor-and-drop scheme: when more than
        // `reward_slots` distinct signers hold roughly equal stake, every base weight floors to
        // 0, and without the leftover round the entire signer set could be dropped.
        let reward_slots = u128::from(pox_constants.reward_slots());
        let threshold = std::cmp::max(1, total_ustx_locked.div_ceil(reward_slots));

        struct Apportionment {
            signing_key: [u8; SIGNERS_PK_LEN],
            stacked_amt: u128,
            weight: u128,
            remainder: u128,
        }

        let mut apportioned: Vec<Apportionment> = signer_set
            .into_iter()
            .map(|(signing_key, stacked_amt)| Apportionment {
                signing_key,
                stacked_amt,
                weight: stacked_amt / threshold,
                remainder: stacked_amt % threshold,
            })
            .collect();

```

**File:** stackslib/src/chainstate/nakamoto/tests/signer_set.rs (L294-327)
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
    for entry in &signer_set {
        assert_eq!(
            entry.weight, 1,
            "each surviving signer should hold one slot"
        );
    }
```

**File:** stackslib/src/chainstate/stacks/tests/reward_set.rs (L151-175)
```rust
        // Test a reward set with not enough participation for any
        // slots to be claimed
        TestVector {
            entries: vec![
                RawRewardSetEntry {
                    reward_address: addrs[0].clone(),
                    amount_stacked: 100_000,
                    stacker: None,
                    signer: None,
                },
                RawRewardSetEntry {
                    reward_address: addrs[1].clone(),
                    amount_stacked: 50_000,
                    stacker: None,
                    signer: None,
                },
                RawRewardSetEntry {
                    reward_address: addrs[0].clone(),
                    amount_stacked: 20_000,
                    stacker: None,
                    signer: None,
                },
            ],
            unstacked_amount: 40_000_000_000_000,
        },
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L3815-3843)
```rust
    #[test]
    // Test with 3 equal signers, and only two sign
    fn test_insufficient_signatures() {
        let signers = [
            (Secp256k1PrivateKey::random(), 100),
            (Secp256k1PrivateKey::random(), 100),
            (Secp256k1PrivateKey::random(), 100),
        ];
        let reward_set = make_reward_set(&signers);

        let mut header = NakamotoBlockHeader::empty();

        // Sign the block with just the first two signers
        let message = header.signer_signature_hash().0;
        let signer_signature = signers
            .iter()
            .take(2)
            .map(|(s, _)| s.sign(&message).expect("Failed to sign block sighash"))
            .collect::<Vec<_>>();

        header.signer_signature = signer_signature;

        match header.verify_signer_signatures(&reward_set, StacksEpochId::latest()) {
            Ok(_) => panic!("Expected insufficient signatures to fail"),
            Err(ChainstateError::InvalidStacksBlock(msg)) => {
                assert!(msg.contains("Not enough signatures"));
            }
            _ => panic!("Expected InvalidStacksBlock error"),
        }
```
