### Title
Empty PoX-4 signer set (`Some(vec![])`) is accepted as a valid Nakamoto reward set, letting blocks bypass signer approval entirely - (File: `stackslib/src/chainstate/nakamoto/coordinator/mod.rs`, `stackslib/src/chainstate/stacks/boot/mod.rs`)

### Summary
This is the closest analog in this repo to the Gravity `updateValset` finding: a threshold/weight computation that can silently produce a degenerate validator (signer) set whose aggregate weight is below the operative threshold, but here the degenerate case is worse — it produces a signer set whose *total weight is zero*, which makes `compute_voting_weight_threshold` return `0`, so **zero signatures satisfy the "70% of signer weight" requirement**. The code that loads/validates Nakamoto reward sets checks only that `signers()` is not `None`; it does not check that `signers()` is non-empty, unlike the sibling PoX-5 path, which does check for exactly this condition.

### Finding Description
`StacksChainState::make_signer_set` (`stackslib/src/chainstate/stacks/boot/mod.rs:1020-1072`) builds the classic (PoX-4) signer set by, for every distinct signing key, computing `weight = stacked_amt / threshold` and filtering out any entry with `weight == 0`: [1](#0-0) 

If `entries` is non-empty (so `entries.first()` succeeds) but *every* individual signer's `stacked_amt` is smaller than `threshold` (e.g., many small stackers, each below one reward slot's worth of STX, while the aggregate threshold computed from total participation is comparatively large), the filter removes every entry and `make_signer_set` returns `Some(vec![])` — not `None`: [2](#0-1) 

This `Some(vec![])` result is embedded directly into the stored `RewardSet` by `make_reward_set` (`stackslib/src/chainstate/stacks/boot/mod.rs:1081-1191`) via `signers: signer_set` and is what gets written to the `.signers` boot contract in `pox_4_compute_and_update_signers` (`stackslib/src/chainstate/nakamoto/signer_set.rs:703-738`) with no emptiness check at all — contrast with the PoX-5 path (`pox_5_compute_and_update_signers`), which explicitly aborts if `signer_set.is_empty()`: [3](#0-2) 

When this reward set is later loaded as the *active* Nakamoto signer set via `OnChainRewardSetProvider::read_reward_set_at_calculated_block` (used by `get_reward_set_nakamoto`, the pure-Nakamoto-cycle code path), the only guard against a degenerate signer set is: [4](#0-3) 
This checks `reward_set.signers().is_none()`, which is `false` for `Some(vec![])`, so the empty-but-`Some` signer set passes through and is accepted as the reward cycle's live signer set. Note that the *other* trait method, `OnChainRewardSetProvider::get_reward_set` (used at the epoch2→Nakamoto boundary), does perform the correct check: [5](#0-4) 
but this check is absent from the pure-Nakamoto `get_reward_set_nakamoto` path that most reward cycles will use.

With this degenerate reward set in place, `NakamotoBlockHeader::verify_signer_signatures` computes: [6](#0-5) 
`total_weight = 0` (sum over an empty signer list), and then: [7](#0-6) 
`threshold = compute_voting_weight_threshold(0) = 0`, and `total_weight_signed` (from an empty `signer_signature` vector) is also `0`. Since `0 < 0` is false, the function returns `Ok(0)` — **the block is accepted with zero signer signatures**, i.e. the entire "70% weighted signer approval" invariant is vacuously satisfied.

This is the direct structural analog of the Gravity `updateValset` bug: a computed weight/threshold pairing that can silently degrade so far that the intended supermajority-approval invariant becomes trivially satisfiable, because nothing checks that the *computed* signer/validator set retains meaningful weight before it is committed and later relied upon by validation.

### Impact Explanation
This breaks the core Nakamoto consensus safety property that a Stacks block must be approved by ≥70% of signer weight before being accepted by nodes and included in the canonical chain. If a degenerate reward cycle (signers `Some(vec![])`) is ever computed and stored, every conforming node in the network will independently compute `threshold = 0` and accept unsigned/arbitrarily-signed blocks for that cycle — this is a network-wide "invalid block accepted" condition (Critical per the stated impact categories), since a block requiring no real signer consent can be produced by any single miner and is treated as valid by the entire network. It effectively neuters the security assumption that ties block finality to stacked-STX-weighted signer consent for the duration of that reward cycle.

### Likelihood Explanation
This requires a specific PoX participation distribution: a non-empty set of PoX-4 stackers/signers where every individual stacker's `stacked_amt` is below the reward-slot `threshold` (computed from aggregate participation and scaled by `POX_MAXIMAL_SCALING`/`POX_THRESHOLD_STEPS_USTX`), so `stacked_amt / threshold == 0` for all of them. Because the classic PoX threshold is a function of *aggregate* liquid/participating STX divided across `reward_slots`, a spread-out but still non-zero participation (many small stackers, no one stacking a full slot's worth) is a plausible, unprivileged/organic condition — no majority collusion or admin key is required to trigger the underlying computation; it only requires stackers behaving normally in a period of low or fragmented participation. This matches the "minority/no-privileged-party-triggerable" bar in the rules.

### Recommendation
- In `read_reward_set_at_calculated_block` (`stackslib/src/chainstate/nakamoto/coordinator/mod.rs`), reject reward sets whose `signers()` is `Some(vec)` with `vec.is_empty()`, mirroring the check already present in `OnChainRewardSetProvider::get_reward_set` and in `pox_5_compute_and_update_signers`.
- Alternatively/also, harden `verify_signer_signatures` and `compute_voting_weight_threshold` to explicitly reject (rather than trivially accept) a reward set with `total_weight == 0`.
- Add the same `is_empty()` guard to the PoX-4 signer computation path (`pox_4_compute_and_update_signers`) that PoX-5 already has, so a degenerate empty signer set is treated as a hard error (e.g., triggering the shadow-block recovery path already designed for "insufficiently many STX locked in PoX") rather than being silently written to `.signers` and later accepted as valid.

### Proof of Concept
1. Configure a PoX-4 reward cycle where total participation is non-zero but fragmented across many stackers, each contributing less than the computed `threshold` (`get_reward_threshold_and_participation`, `stackslib/src/chainstate/stacks/boot/mod.rs:1211-1243`) — e.g., `threshold` computed from `liquid_ustx / POX_MAXIMAL_SCALING` scaled across `reward_slots`, while each individual stacker's amount is smaller than that per-slot threshold.
2. `StacksChainState::make_signer_set` filters every entry to `weight == 0` and returns `Some(vec![])` (`stackslib/src/chainstate/stacks/boot/mod.rs:1051-1065`).
3. `pox_4_compute_and_update_signers` writes this reward set (including the empty-but-`Some` signer list) via `update_signers`/`.signers` contract with no emptiness check (`stackslib/src/chainstate/nakamoto/signer_set.rs:703-738`).
4. When the corresponding reward cycle starts, `load_nakamoto_reward_set` → `OnChainRewardSetProvider::get_reward_set_nakamoto` → `read_reward_set_at_calculated_block` loads this reward set, passing the `signers().is_none()` check because it's `Some(vec![])` (`stackslib/src/chainstate/nakamoto/coordinator/mod.rs:239-245`).
5. A miner proposes a Nakamoto block for this cycle with an empty `signer_signature` vector.
6. `NakamotoBlockHeader::verify_signer_signatures` computes `total_weight = 0`, `threshold = compute_voting_weight_threshold(0) = 0`, `total_weight_signed = 0`, and returns `Ok(0)` — the block passes signature verification with **no signer approval at all** (`stackslib/src/chainstate/nakamoto/mod.rs:1122-1189`).

Note: I was not able to execute this scenario end-to-end (no test harness run), so this is derived from static code-path analysis of the cited functions; confirming the exact numeric conditions under which `get_reward_threshold_and_participation` yields a `threshold` exceeding every individual stacker's `amount_stacked` would benefit from a concrete unit/integration test run, which a background Devin session with repo execution access could perform.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1024-1037)
```rust
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
```

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

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L759-766)
```rust
            signer_set,
            pox_ustx_threshold,
        } = Self::pox_5_make_signer_set(&mut entries, pox_constants)?;

        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L239-245)
```rust
        if reward_set.signers().is_none() {
            err_or_debug!(
                debug_log,
                "FATAL: PoX reward set did not specify signer set in Nakamoto"
            );
            return Err(Error::PoXAnchorBlockRequired);
        }
```

**File:** stackslib/src/chainstate/coordinator/mod.rs (L349-352)
```rust
        if is_nakamoto_reward_set && reward_set.signers().map_or(true, |s| s.is_empty()) {
            error!("FATAL: Signer sets are empty in a reward set that will be used in nakamoto"; "reward_set" => ?reward_set);
            return Err(Error::PoXAnchorBlockRequired);
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1122-1124)
```rust
        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1187)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }
```
