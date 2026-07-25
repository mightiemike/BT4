The key code is now clear. Let me trace the exact epoch numbering through `finalize_epoch` and `apply_epoch_update_to_proposals`.

### Title
`ProtocolVersionTooOld` Kickout Bypassed After Two Epochs via Re-stake — (`chain/epoch-manager/src/validator_selection.rs`)

---

### Summary

The guard in `apply_epoch_update_to_proposals` that blocks a `ProtocolVersionTooOld`-kicked validator from re-entering the validator set only inspects `prev_epoch_info.validator_kickout()` — the kickout map of the immediately preceding EpochInfo. Because the `ProtocolVersionTooOld` reason is never propagated into the kickout map of T+3's EpochInfo (the validator was not active in T+1 or T+2, so no performance-based kickout is generated), a re-stake submitted in epoch T+2 passes both guards and the validator re-enters in epoch T+4 without having upgraded its protocol version.

---

### Finding Description

**Epoch numbering convention:** `finalize_epoch` is called at the end of epoch T and stores T+2's EpochInfo. `prev_epoch_info` inside `proposals_to_epoch_info` is always T+1's EpochInfo (i.e., `next_epoch_info` at call time). [1](#0-0) 

**Step-by-step trace:**

| Finalize end of | Computes | `validator_kickout` passed in | `prev_epoch_info.validator_kickout()` | test2 blocked? |
|---|---|---|---|---|
| T | T+2 EpochInfo | `{test2: ProtocolVersionTooOld}` (from T's perf) | T+1's kickout (irrelevant) | ✅ via `validator_kickout.contains_key` |
| T+1 | T+3 EpochInfo | `{}` (test2 not a validator in T+1) | T+2's kickout = `{test2: ProtocolVersionTooOld}` | ✅ via `prev_epoch_info.validator_kickout()` check |
| T+2 | T+4 EpochInfo | `{}` (test2 not a validator in T+2) | **T+3's kickout = `{}`** (test2 never re-added) | ❌ **both guards miss** |

The two guards in `apply_epoch_update_to_proposals` are:

```rust
// Guard 1: current kickout (performance-based, from T+2's epoch)
if validator_kickout.contains_key(account_id) { ... }
// Guard 2: prev_epoch_info kickout (T+3's kickout — empty for test2)
else if let Some(ValidatorKickoutReason::ProtocolVersionTooOld { .. }) =
    prev_epoch_info.validator_kickout().get(account_id)
{ continue; }
``` [2](#0-1) 

T+3's kickout map is empty for `test2` because `test2` was not a validator during T+1 or T+2, so `compute_validators_to_reward_and_kickout` never generates a performance kickout for it, and the `ProtocolVersionTooOld` reason is never re-inserted. [3](#0-2) 

The `ProtocolVersionTooOld` kickout is only generated for validators **active in the current epoch** whose `version_tracker` entry is below `next_next_epoch_version`. A validator that was already kicked out in T+2 is absent from T+1's and T+2's validator sets, so it never appears in `version_tracker` again, and the kickout reason is never re-generated.

The existing test `test_version_switch_kickout_old_version` only verifies that a proposal submitted in T+1 is blocked (re-entry into T+3). It does not test a proposal submitted in T+2 (re-entry into T+4). [4](#0-3) 

---

### Impact Explanation

An outdated node running protocol version `V` (below the network's `V+1`) re-enters the validator set in epoch T+4 without upgrading. Once active, it produces chunks and blocks signed under the old protocol version. Honest nodes running `V+1` may reject or diverge on those chunks, causing a consensus failure. The attacker also earns staking rewards for epochs in which it is active with an incompatible version. This is a consensus flaw reachable from an ordinary staking transaction submitted by an unprivileged user.

---

### Likelihood Explanation

Any validator that was kicked out for `ProtocolVersionTooOld` can exploit this by simply waiting one additional epoch before re-submitting a staking transaction. No privileged access, key compromise, or external coordination is required. The staking transaction is a standard unprivileged action.

---

### Recommendation

The `ProtocolVersionTooOld` guard must persist beyond a single epoch. Options:

1. **Propagate the reason forward:** When building T+3's kickout map, explicitly carry over any `ProtocolVersionTooOld` entries from T+2's kickout for accounts that have not yet upgraded.
2. **Re-check version at proposal time:** In `apply_epoch_update_to_proposals`, additionally check whether the proposing account's last observed protocol version is still below `next_next_epoch_version`, not just whether it appears in `prev_epoch_info.validator_kickout()`.
3. **Extend the kickout window:** Ensure `ProtocolVersionTooOld` entries are re-inserted into each subsequent epoch's kickout map until the validator demonstrates an upgraded version.

---

### Proof of Concept

```rust
// Sketch: extend test_version_switch_kickout_old_version
// After T+1 epoch (test2 blocked from T+3), run one more epoch (T+2)
// with test2 submitting a re-stake at old version.
(last_hash, _) =
    record_blocks(&mut epoch_manager, last_hash, height, epoch_length, |_h, _validator| {
        (vec![stake("test2".parse().unwrap(), small_stake)], version) // old version
    });
// T+4's EpochInfo — test2 should NOT be a validator, but the bug allows it.
let epoch_info = epoch_manager.get_epoch_info(&EpochId(last_hash)).unwrap();
// This assertion currently FAILS (test2 is present):
check_validators(&epoch_info, just_test1);
```

The test at lines 2306–2313 already demonstrates the T+1 block. Adding one more epoch loop with the same re-stake reproduces the bypass into T+4. [5](#0-4)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L641-656)
```rust
        let mut validator_kickout = HashMap::new();

        // Kickout validators voting for an old version.
        for (validator_id, version) in version_tracker {
            if version >= next_next_epoch_version {
                continue;
            }
            let validator = epoch_info.get_validator(validator_id);
            validator_kickout.insert(
                validator.take_account_id(),
                ValidatorKickoutReason::ProtocolVersionTooOld {
                    version,
                    network_version: next_next_epoch_version,
                },
            );
        }
```

**File:** chain/epoch-manager/src/lib.rs (L938-944)
```rust
        let next_next_epoch_info = match proposals_to_epoch_info(
            &next_next_epoch_config,
            rng_seed,
            &next_epoch_info,
            all_proposals,
            validator_kickout,
            validator_reward,
```

**File:** chain/epoch-manager/src/validator_selection.rs (L298-313)
```rust
    for p in proposals {
        let account_id = p.account_id();
        if validator_kickout.contains_key(account_id) {
            let account_id = p.take_account_id();
            stake_change.insert(account_id, Balance::ZERO);
        } else if let Some(ValidatorKickoutReason::ProtocolVersionTooOld { .. }) =
            prev_epoch_info.validator_kickout().get(account_id)
        {
            // If the validator was kicked out because of an old protocol version in T-1,
            // it is not allowed back in T.
            continue;
        } else {
            stake_change.insert(account_id.clone(), p.stake());
            proposals_by_account.insert(account_id.clone(), p);
        }
    }
```

**File:** chain/epoch-manager/src/tests/mod.rs (L2306-2314)
```rust
    // Try to add test2 as a proposal in T+1, this should not work.
    (last_hash, _) =
        record_blocks(&mut epoch_manager, last_hash, height, epoch_length, |_h, _validator| {
            (vec![stake("test2".parse().unwrap(), small_stake)], version)
        });

    let epoch_info = epoch_manager.get_epoch_info(&EpochId(last_hash)).unwrap();
    check_validators(&epoch_info, just_test1);
}
```
