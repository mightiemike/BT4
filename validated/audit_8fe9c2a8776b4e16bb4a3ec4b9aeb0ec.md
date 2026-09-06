## Analysis

The reported pattern in the external report is: a per‑unit divisor (`exchangeRateBps`/`1e4`) can force an individual contribution to floor‑divide to zero, which — combined with a threshold check that assumes at least one non‑zero unit — locks up the whole system (crowdfund can neither finalize nor be safely completed).

The stacks-core repository has a structurally identical rounding defect in the *pre‑PoX‑5* signer‑weight apportionment routine, `StacksChainState::make_signer_set` in `stackslib/src/chainstate/stacks/boot/mod.rs`, which is still the code path used for PoX‑4 signer‑set computation (`Self::make_signer_set(threshold, &addresses)` at [1](#0-0) ), called from `pox_4_compute_and_update_signers` in `stackslib/src/chainstate/nakamoto/signer_set.rs` ( [2](#0-1) ).

The vulnerable function computes each signer's weight as a plain floor division and drops any signer whose weight floors to zero, with no remainder-based reallocation: [3](#0-2) 

The exact same defect was identified and fixed for PoX‑5 in `pox_5_make_signer_set` (`stackslib/src/chainstate/nakamoto/signer_set.rs`), which explicitly documents the failure mode in comments and adds a largest‑remainder ("Hare") correction round to guarantee the signer set is never emptied by rounding: [4](#0-3) 

A regression test for the PoX‑5 fix documents precisely this class of bug and confirms it previously "stalled the chain": [5](#0-4) 

Since PoX‑4's `make_signer_set` was never given this correction, the same collapse condition remains reachable through ordinary, unprivileged `stack-stx` calls, whose only floor is the *absolute* per‑address minimum `get-stacking-minimum`, independent of the number of reward slots: [6](#0-5) [7](#0-6) 

### Title
Floor-and-drop rounding in PoX-4 `make_signer_set` can zero out the entire signer set for a reward cycle - (File: `stackslib/src/chainstate/stacks/boot/mod.rs`)

### Summary
`StacksChainState::make_signer_set` assigns each PoX‑4 signer a weight of `stacked_amt / threshold` and silently discards any entry whose weight floors to `0`, with no remainder redistribution. If the number of distinct signer addresses exceeds `reward_slots` while their per‑address stakes are close to the derived `threshold`, every individual `stacked_amt / threshold` can floor to `0` simultaneously, dropping every signer and producing an empty signer set for the whole reward cycle.

### Finding Description
`make_reward_set` computes `threshold` from aggregate participation via `get_reward_threshold_and_participation`/`get_threshold_from_participation` (`scale_by / reward_slots`, rounded up to the nearest step) [8](#0-7) , then calls `make_signer_set(threshold, &addresses)` [1](#0-0) , which for each signer computes `weight = u32::try_from(stacked_amt / threshold)` and filters `weight == 0` entries out of the returned signer set [9](#0-8) .

Because `threshold` is derived from *total* participation divided by `reward_slots`, and each individual stacker's minimum required stake (`get-stacking-minimum = stx-liquid-supply / STACKING_THRESHOLD_25`) is independent of how many distinct addresses participate, an unprivileged actor can register more than `reward_slots` distinct signer addresses, each individually stacking just above the absolute minimum but below the reward-cycle `threshold`. Every entry's `stacked_amt / threshold` then floors to `0`, and every entry is filtered out — leaving `signer_set` (and thus the returned `RewardSet`'s `signers`) empty, even though real, non-zero STX is locked.

This is the exact rounding-to-zero collapse that the PoX‑5 code path (`pox_5_make_signer_set`) was specifically patched to avoid, using a largest-remainder ("Hare") allocation so that base weights summing below `reward_slots` are backfilled from leftover slots instead of dropping every signer [4](#0-3) . The regression test added alongside that fix documents the same failure mode by name ("stalling the chain") for the "old floor-and-drop scheme" [10](#0-9) . `make_signer_set` (PoX‑4 path) received no equivalent fix.

### Impact Explanation
An empty signer set for a PoX‑4 reward cycle breaks the invariant that Nakamoto blocks for that cycle can be validly signed and approved: `verify_signer_signatures`/`compute_voting_weight_threshold` operate over a `total_signing_weight()` of `0`, and downstream consumers either error out (`NoRegisteredSigners`) or the coordinator falls back to defaults, but in either case no legitimate stacker in the cycle can accrue signing weight proportional to their locked STX. This is a minority-triggerable divergence between the intended "any staker with sufficient locked STX participates in signing" invariant and the actual outcome ("everyone is dropped"), producing a stall/tip disagreement for that reward cycle bounded to the affected cycle — matching the High-impact category of a minority-triggerable signer-weight/threshold divergence.

### Likelihood Explanation
The attack requires no majority of stake — only enough distinct addresses (`> reward_slots`) each individually meeting the absolute `get-stacking-minimum` floor, which is decoupled from `reward_slots`. This is entirely achievable by a single unprivileged party splitting a modest amount of STX across many addresses via ordinary `stack-stx` calls, with no admin/operator/other-party key required.

### Recommendation
Port the largest-remainder ("Hare") apportionment used in `pox_5_make_signer_set` back into `make_signer_set` (or replace its call sites), so that base weights (`floor(stacked/threshold)`) are backfilled from leftover reward slots by descending remainder instead of unconditionally dropping every zero-weight entry when aggregate flooring collapses the whole set.

### Proof of Concept
1. Let `reward_slots = R` and compute `threshold` from total participation as today.
2. Register `N > R` distinct signer addresses (unprivileged `stack-stx` calls) each stacking an amount `s` such that `s < threshold` but `s * N ≈` total participation used to derive `threshold`.
3. In `make_signer_set`, every entry's `stacked_amt / threshold` evaluates to `0`, so every entry is filtered by the `weight == 0` check [11](#0-10) .
4. The resulting `RewardSet.signers` is empty for the cycle, despite non-zero real participation — directly reproducing the collapse scenario the PoX‑5 regression test `equal_stakes_exceeding_reward_slots_are_not_all_zeroed` was written to prevent [5](#0-4) , but the PoX‑4 path lacks that protection.

### Citations

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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1085-1095)
```rust
    ) -> RewardSet {
        let mut reward_set = vec![];
        let mut missed_slots = vec![];
        // the way that we sum addresses relies on sorting.
        if epoch_id < StacksEpochId::Epoch21 {
            addresses.sort_by_cached_key(|k| k.reward_address.bytes());
        } else {
            addresses.sort_by_cached_key(|k| k.reward_address.to_burnchain_repr());
        }

        let signer_set = Self::make_signer_set(threshold, &addresses);
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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L703-722)
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
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L858-911)
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

        // Guaranteed `<= reward_slots` by the ceil quota, so leftover does not underflow.
        let assigned: u128 = apportioned.iter().map(|entry| entry.weight).sum();
        let mut leftover = reward_slots.saturating_sub(assigned);

        if leftover > 0 {
            // Largest fractional remainder wins the next slot; ties broken by signing_key
            // ascending so the apportionment is deterministic (and matches the final sort).
            apportioned.sort_by(|a, b| {
                b.remainder
                    .cmp(&a.remainder)
                    .then_with(|| a.signing_key.cmp(&b.signing_key))
            });
            for entry in apportioned.iter_mut() {
                if leftover == 0 {
                    break;
                }
                entry.weight += 1;
                leftover -= 1;
            }
        }
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

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L468-471)
```text
;; What is the minimum number of uSTX to be stacked in the given reward cycle?
;; Used internally by the Stacks node, and visible publicly.
(define-read-only (get-stacking-minimum)
    (/ stx-liquid-supply STACKING_THRESHOLD_25))
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L494-503)
```text
(define-read-only (can-stack-stx (pox-addr (tuple (version (buff 1)) (hashbytes (buff 32))))
                                  (amount-ustx uint)
                                  (first-reward-cycle uint)
                                  (num-cycles uint))
  (begin
    ;; minimum uSTX must be met
    (asserts! (<= (get-stacking-minimum) amount-ustx)
              (err ERR_STACKING_THRESHOLD_NOT_MET))

    (minimal-can-stack-stx pox-addr amount-ustx first-reward-cycle num-cycles)))
```
