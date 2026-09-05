The strongest analog to the vault "donation attack" in this codebase is the **PoX-4 signer-weight apportionment function `StacksChainState::make_signer_set`**, which uses the exact "floor-and-drop" division pattern that the Stacks team itself later identified as broken and explicitly fixed for PoX-5 (`NakamotoSigners::pox_5_make_signer_set`), but left unfixed for PoX-4.

### Title
PoX-4 signer-weight computation drops legitimate stackers to zero weight via floor-division rounding, unlike the patched PoX-5 equivalent - (File: stackslib/src/chainstate/stacks/boot/mod.rs)

### Summary
`StacksChainState::make_signer_set` computes each signer's block-approval weight as `stacked_amt / threshold` and silently filters out (drops to zero participation) any entry whose floor division rounds to `0`, with no compensating mechanism. This is the same "round-to-zero on division" defect described in the donation-attack report: whenever the shared `threshold` denominator is large relative to an individual stacker's `stacked_amt` (which any single, unprivileged stacker can influence, since `threshold` scales with aggregate `participation`/`liquid_ustx`), that stacker's weight is truncated to `0` and they are excised entirely from the signer set for that reward cycle, losing all block-signing weight, with no redistribution of the lost weight.

### Finding Description
`make_signer_set` sums each `signing_key`'s stacked amount and computes `weight = stacked_amt / threshold`, dropping the entry entirely if `weight == 0`. [1](#0-0) 

`threshold` itself is a dynamic, participation-scaled value computed by `get_reward_threshold_and_participation`, whose denominator (`scale_by`) grows with the total amount stacked in the cycle: [2](#0-1) 

This is invoked from `pox_4_compute_and_update_signers`, which is the live signer-set-computation path for PoX-4 reward cycles in Nakamoto: [3](#0-2) 

Contrast this with `NakamotoSigners::pox_5_make_signer_set`, which uses the identical `stacked_amt / threshold` floor-division starting point but explicitly guards against the exact failure mode with a largest-remainder ("Hare quota") redistribution of leftover slots, with an inline comment describing precisely this bug class: [4](#0-3) 

The comment explicitly states: "This avoids degenerate modes of the floor-and-drop scheme: when more than `reward_slots` distinct signers hold roughly equal stake, every base weight floors to 0, and without the leftover round the entire signer set could be dropped." That is a direct acknowledgment that the PoX-4 `make_signer_set` scheme (which has no leftover round) is subject to this defect — it is simply unpatched there.

The equality broken is: *a stacker's assigned signing weight should be proportional to their legitimately stacked amount, and total assigned weight should conserve the intended reward-slot allocation.* Instead, weight can be silently truncated to zero and lost (not redistributed) for stackers whose `stacked_amt` legitimately falls just under an inflated `threshold`, purely as a side effect of other unprivileged stackers' participation increasing the shared denominator — a minority-triggerable, unprivileged action analogous to the donation attack's ratio distortion.

### Impact Explanation
Any stacker whose weight floors to zero is completely excluded from the Nakamoto block-approval signer set for that reward cycle, losing all block-validation weight and any weight-tied signer rewards, with zero recourse and no on-chain remedy for that cycle. In the degenerate case (many similarly-sized stackers relative to `reward_slots`), the *entire* signer set can compute to weight `0`, which triggers the fatal, chain-halting condition already documented for the PoX-5 case: [5](#0-4) 
For PoX-4 this manifests as reward-slot loss for the affected stacker (bounded, per-cycle mis-payment), consistent with the "High" bucket ("a poison or reward mis-payment bounded to fees").

### Likelihood Explanation
No privileged access or majority coordination is required — a single, unprivileged stacker's ordinary PoX stacking transaction increases `participation`, which increases `threshold` for everyone, which can push other legitimately-stacked, minority participants below the new threshold. This is a natural and even unintentional consequence of the floor-and-drop scheme, so likelihood is non-trivial in any cycle with several similarly-sized stackers.

### Recommendation
Apply the same largest-remainder (Hare quota) leftover-redistribution scheme used in `pox_5_make_signer_set` to `make_signer_set`, ensuring conservation of total assigned weight against `reward_slots` and guaranteeing every stacker with a nonzero base weight retains representation, matching the "Guard Against Zero Share Minting" remediation pattern from the original report.

### Proof of Concept
1. Compute `threshold` via `get_reward_threshold_and_participation` for a set of `N` stackers whose individual `stacked_amt` values are all just below `threshold` but whose sum exceeds it (e.g., `N` stackers each staking `threshold - 1` microSTX).
2. Call `StacksChainState::make_signer_set(threshold, addresses)` (as invoked from `pox_4_compute_and_update_signers`).
3. Observe that every entry computes `weight = stacked_amt / threshold == 0` and is filtered out, per the loop in `mod.rs` lines 1051–1065, yielding either an empty signer set (triggering `PoxNoRewardCycle` in the PoX-5 codepath's equivalent check, or silent signer-set shrinkage in PoX-4) — with no leftover-weight compensation, unlike `pox_5_make_signer_set`'s tested behavior (`weight_zero_entries_are_filtered` in `stackslib/src/chainstate/nakamoto/tests/signer_set.rs` lines 263–292, which passes precisely because the Hare round exists there but not in `make_signer_set`).

### Citations

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1051-1065)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L703-723)
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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L763-766)
```rust
        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
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
