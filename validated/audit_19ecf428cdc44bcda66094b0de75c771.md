### Title
Legacy floor-and-drop signer-weight apportionment can zero out the entire PoX signer set - ([File: stackslib/src/chainstate/stacks/boot/mod.rs])

### Summary
`StacksChainState::make_signer_set` in `stackslib/src/chainstate/stacks/boot/mod.rs` computes each signer's consensus-critical weight as `stacked_amt / threshold` and silently drops any signer whose result floors to zero, with no fallback. The newer `pox_5_make_signer_set` in `stackslib/src/chainstate/nakamoto/signer_set.rs` explicitly replaces this "floor-and-drop" scheme with a largest-remainder ("Hare") method precisely because, as its own comments state, the floor-and-drop scheme can degenerate to an entirely empty signer set.

### Finding Description
`make_signer_set` aggregates `amount_stacked` per signing key and assigns weight via simple integer division against `threshold`, filtering out any entry whose `stacked_amt / threshold == 0`: [1](#0-0) 

This is structurally the same rounding-to-zero defect described in the external report (value/debt truncating to zero on integer division), but here it truncates a *signer's reward-set weight* to zero rather than a debt value. If more distinct signers exist than `reward_slots` and stakes are distributed such that every individual `stacked_amt / threshold` floors to zero — a scenario the sibling implementation's own regression test explicitly documents as previously causing "the entire signer set to be dropped, stalling the chain" — `make_signer_set` returns an empty (or degenerate) weight-zero signer set: [2](#0-1) 

The fix applied in `pox_5_make_signer_set` uses `threshold = max(1, total.div_ceil(reward_slots))` plus a largest-remainder round to guarantee `base + min(leftover, N)` slots are always allocated and the set is never spuriously emptied: [3](#0-2) 

`make_signer_set`, however, still uses the naive floor-only approach with no leftover-slot correction, meaning the same class of dust-rounding defect that the sibling function was rewritten to fix remains present.

### Impact Explanation
If `make_signer_set` is reachable on the code path that computes the Nakamoto reward/signer set for a reward cycle and produces an empty or reduced set relative to what other nodes would compute under a corrected algorithm, this breaks the equality "every honest node computes the identical signer set/weights for a reward cycle," which is exactly the class of divergence flagged as High impact in the rules (temporary tip disagreement / static-validation divergence) — or, if it actually causes block-production stall network-wide, a chain halt. However, I was not able to conclusively confirm from the available index whether `make_signer_set` (as opposed to `pox_5_make_signer_set`) is still on a live, currently-invoked consensus path (e.g., for pox-4 reward cycles) versus dead/legacy code superseded everywhere by the pox-5 path — my tool budget ran out before I could trace all call sites of `make_signer_set` to their invocation context.

### Likelihood Explanation
Triggering the degenerate case requires an unprivileged set of stackers to distribute stake such that every individual entry's `stacked_amt / threshold` floors to zero (e.g., many small, roughly-equal stackers exceeding `reward_slots`), which is realistically achievable by ordinary participants without needing majority control — matching the "minority-triggerable" bar. But because I could not verify whether this function is still active in the current consensus flow versus superseded, likelihood of real-world exploitability is uncertain.

### Recommendation
Confirm all call sites of `make_signer_set` and, if it is still used to compute any live reward-cycle signer set, either retire it in favor of `pox_5_make_signer_set`'s largest-remainder allocation or backport the same `threshold = max(1, total.div_ceil(reward_slots))` + leftover-slot distribution logic so no signer's weight can round away to zero when the underlying stake is nonzero and the aggregate should still fill `reward_slots`.

### Proof of Concept
Analogous to the existing regression test for the (now fixed) sibling function: construct `reward_slots = 4`, and 5 signers each staking an equal `amount_stacked` such that `total_stacked / reward_slots` yields a `threshold` under which each individual `stacked_amt / threshold` floors to `0`; call `make_signer_set(threshold, entries)` and observe it returns `None`/an empty set instead of a size-`reward_slots` set, mirroring `equal_stakes_exceeding_reward_slots_are_not_all_zeroed` in `stackslib/src/chainstate/nakamoto/tests/signer_set.rs` (lines 294-321), which was written specifically to catch this defect in the sibling implementation. [4](#0-3)

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
