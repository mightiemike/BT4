### Title
PoX-4/legacy signer-set computation can floor every signer's weight to zero and drop the entire signer set, stalling block production - (File: `stackslib/src/chainstate/stacks/boot/mod.rs`)

### Summary
`StacksChainState::make_signer_set` (used by the PoX-4 reward-set path via `pox_4_compute_and_update_signers`) computes each signer's weight as `floor(stacked_amt / threshold)` and filters out any signer whose weight floors to `0`, with no leftover-slot redistribution. A newer sibling function, `pox_5_make_signer_set`, was patched with an explicit "Hare round" largest-remainder allocation specifically because this floor-and-drop scheme could zero out every signer and drop the whole set when more distinct signers exist than `reward_slots`, each holding roughly equal stake. That fix was applied only to the PoX-5 path; the PoX-4/legacy `make_signer_set` used today still has the original defect.

### Finding Description
`get_reward_threshold_and_participation` computes `threshold` by scaling `liquid_ustx`/`participation` and rounding up to the nearest `POX_THRESHOLD_STEPS_USTX` step [1](#0-0) . This threshold, together with the raw stacker/signer entries, is fed into `make_signer_set`, which computes weight as `stacked_amt / threshold` (integer floor) and drops any signer whose result is `0`: [2](#0-1) 

Unlike `pox_5_make_signer_set`, there is no largest-remainder ("Hare round") step to redistribute leftover slots to signers whose floor computation reached `0`. The regression test explicitly added for the PoX-5 version documents the exact failure mode this repo's PoX-4 path is still exposed to: [3](#0-2) [4](#0-3) 

If a set of unprivileged stackers register with distinct signing keys and near-equal stacked amounts such that the number of distinct signers exceeds `reward_slots` while each individual amount, when divided by the computed `threshold`, floors to `0`, `make_signer_set` returns a signer set that is empty (`signer_set.is_empty()`), or at minimum omits legitimate participants entirely — with no minimum-weight guarantee. This flows into `pox_4_compute_and_update_signers`, which calls `update_signers` with `reward_set.signers().unwrap_or(&empty_signers)`: [5](#0-4) 

An empty signer set written to `.signers` for a reward cycle later causes `read_reward_set_at_calculated_block`/`load_signer_set` to treat the cycle as having no valid signer set (`Error::PoXAnchorBlockRequired` / `NakamotoNodeError::SigningCoordinatorFailure`), which prevents miners from mining and blocks the whole network from producing new Nakamoto blocks for that reward cycle: [6](#0-5) [7](#0-6) 

This is the exact "malicious/uncooperative party makes a shared resource unusable for everyone" pattern from the ERC1155-buyout report, generalized: the equality broken is "the on-chain signer set for a reward cycle == the set of signers who legitimately staked/registered." A minority of stackers (who need no special privilege — just the ability to register as distinct signing keys with roughly equal, evenly-distributed stake) can force this equality to break by making `make_signer_set` compute an empty or wrongly-truncated set, unlike the honest expectation that everyone who registered above a fair share gets counted.

### Impact Explanation
An empty or degenerate signer set for a PoX-4 reward cycle halts Nakamoto block production network-wide for that cycle (a chain-wide stall), matching the "High" tier: "a minority-triggerable sortition/VRF/static-validation divergence... temporary tip disagreement," and potentially rising to "Critical" (irreversible reorg / stall) depending on how long the affected reward cycle persists without recovery, since there is no documented fallback once `.signers` has been written empty for a cycle.

### Likelihood Explanation
Triggering requires only that a set of ordinary, unprivileged stackers register more distinct signing keys than there are `reward_slots`, with stake distributed such that every individual floor(`stacked/threshold`) equals `0` — this is a function of public, attacker-controllable parameters (number of distinct signer keys, stake amounts) and does not require a majority of stake or any special permission. The existence of a dedicated regression test and code comment in the PoX-5 sibling function confirms the Stacks team already identified and fixed this exact bug class, but only in the newer code path — the PoX-4 path (`make_signer_set` in `boot/mod.rs`) retains the vulnerable original logic.

### Recommendation
Port the largest-remainder ("Hare round") allocation from `pox_5_make_signer_set` into the legacy `StacksChainState::make_signer_set` (or otherwise guarantee that `reward_slots` weight is fully and fairly apportioned even when many signers' raw `stacked/threshold` floors to zero), so that PoX-4/legacy reward-cycle signer sets can never be unintentionally emptied by evenly distributed stake among more than `reward_slots` distinct signers.

### Proof of Concept
1. Compute (or observe) the PoX-4 reward-cycle `threshold` via `get_reward_threshold_and_participation` for a given `liquid_ustx`/`participation` [1](#0-0) .
2. Register `reward_slots + 1` (or more) distinct signing keys via PoX-4, each stacking an amount `A` such that `A / threshold == 0` for every individual key even though the aggregate is well above the participation floor (mirroring the unit test constructed for PoX-5): [3](#0-2) .
3. When `pox_4_compute_and_update_signers` runs `make_signer_set(threshold, entries)`, every entry's weight floors to `0` and is filtered out [8](#0-7) , yielding `signers: None`/empty for the reward cycle.
4. `read_reward_set_at_calculated_block` then rejects the cycle with `Error::PoXAnchorBlockRequired` [9](#0-8) , and miner threads fail to load a signer set, halting block production for that reward cycle [7](#0-6) .

### Citations

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1039-1071)
```rust
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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1211-1242)
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
```

**File:** stackslib/src/chainstate/nakamoto/tests/signer_set.rs (L294-335)
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
    let total_weight: u128 = signer_set.iter().map(|e| u128::from(e.weight)).sum();
    assert_eq!(total_weight, u128::from(reward_slots));
    // Ties broken by signing_key ascending: keys 0x00..0x03 win, 0x04 is dropped.
    assert!(
        !signer_set.iter().any(|e| e.signing_key == signer_key(0x04)),
        "highest-key signer should be the one dropped on tie-break"
    );
}
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L699-738)
```rust
    /// For PoX-4, compute the reward set for the next reward cycle,
    /// store it, and write it to the .signers contract.
    ///
    /// * `reward_cycle` is the reward cycle for the calculation (i.e., the next cycle).
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

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L204-228)
```rust
        let Some(reward_set) = NakamotoChainState::get_reward_set(
            chainstate.db(),
            &reward_set_block.index_block_hash(),
        )?
        else {
            err_or_debug!(
                debug_log,
                "No reward set stored at the block in which .signers was written";
                "checked_block" => %reward_set_block.index_block_hash(),
                "coinbase_height_of_calculation" => coinbase_height_of_calculation,
            );
            return Err(Error::PoXAnchorBlockRequired);
        };

        // This method should only ever called if the current reward cycle is a nakamoto reward cycle
        //  (i.e., its reward set is fetched for determining signer sets (and therefore agg keys).
        //  Non participation is fatal.
        if reward_set
            .rewarded_addresses()
            .map_or(false, |addrs| addrs.is_empty())
        {
            // no one is stacking (V0 with empty rewarded_addresses)
            err_or_debug!(debug_log, "No PoX participation");
            return Err(Error::PoXAnchorBlockRequired);
        }
```

**File:** stacks-node/src/nakamoto_node/miner.rs (L1160-1171)
```rust
        ) {
            Ok(Some(reward_set)) => reward_set,
            Ok(None) => {
                return Err(NakamotoNodeError::SigningCoordinatorFailure(
                    "No reward set stored yet. Cannot mine!".into(),
                ));
            }
            Err(ChainstateError::NoRegisteredSigners(..)) => {
                return Err(NakamotoNodeError::SigningCoordinatorFailure(
                    "Current reward cycle did not select a reward set. Cannot mine!".into(),
                ));
            }
```
