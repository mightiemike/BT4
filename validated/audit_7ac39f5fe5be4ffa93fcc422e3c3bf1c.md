### Title
Loss of Accrued Bond Rewards on Signer Reassignment via Unsettled `remove-staker-from-bond-for-cycle` - (File: stackslib/src/chainstate/stacks/boot/pox-5.clar)

### Summary
`update-bond-registration` moves a protocol-bond participant's sBTC shares from an old signer to a new signer by calling `remove-staker-from-bond-cycles` → `remove-staker-from-bond-for-cycle`. Unlike every other staking-mutation path in the contract, this function zeroes out `staker-shares-staked-for-cycle` and decrements `signer-shares-staked-for-cycle`/`total-shares-staked-for-cycle` without first calling `settle-rewards`/`settle-staker-rewards` for the cycle being mutated (beyond `current-cycle`, which is separately settled once in the caller). This mirrors the exact bug class described in the external report: state is removed from reward tracking before the accrued reward is settled/claimed, permanently losing that reward for the "current-cycle" bond flow whenever `bond-index`'s rewards-per-token advances between settlement and the fold that zeroes shares.

### Finding Description
`update-bond-registration` (pox-5.clar:850-943) settles rewards **once**, for `current-cycle` only, at lines 895-901: [1](#0-0) 

It then calls `remove-staker-from-bond-cycles` (which folds `remove-staker-from-bond-for-cycle` over `[first_reward_cycle, first_reward_cycle+num_cycles)`): [2](#0-1) 

`remove-staker-from-bond-for-cycle` itself performs **no** settlement call — contrast this with its sibling functions, which always settle before mutating shares:

- `remove-staker-from-signer-for-cycle` (STX-only unstake path) explicitly settles both signer- and staker-level rewards before mutating `signer-shares-staked-for-cycle`/`staker-shares-staked-for-cycle`: [3](#0-2) 

- `unstake-sats-from-bond-cycle` (the direct sBTC-unstake path) also settles before mutating: [4](#0-3) 

But `remove-staker-from-bond-for-cycle`, used only by the signer-reassignment path, goes straight to mutation with no settlement: [5](#0-4) 

The design invariant documented at `settle-rewards`'s own comment states this MUST be called before any update to `signer-shares-staked-for-cycle`: [6](#0-5) 

`remove-staker-from-bond-for-cycle` violates this invariant for `current-signer`/`bond-index` at `reward-cycle = current-cycle` in the following sequence within a single `update-bond-registration` call:
1. Line 896 settles `current-signer`'s bond rewards-per-token snapshot as of *before* the reassignment (call this settlement S1, capturing `rewards-per-token = R0`).
2. Any subsequent trait calls inside the same transaction that could advance `signer-rewards-per-token-for-cycle` for `current-signer`/`bond-index` (e.g., `settle-rewards` being re-triggered by another staker's action reentering `update-bond-registration`, or by `calculate-rewards`/`claim-rewards` being invoked within the same block by another party affecting the same `bond-index`) would move `rewards-per-token` to `R1 > R0` before this staker's `remove-staker-from-bond-for-cycle` runs and permanently zeroes `staker-shares-staked-for-cycle` to `u0` — at that point the staker's un-settled delta `(R1 - R0) * shares` for that cycle can never be recovered, because `settle-rewards`/`settle-staker-rewards` are never called again for this staker/cycle/bond-index combination by this transaction, and `staker-shares-staked-for-cycle` is now `u0`, so any later `settle-staker-rewards` call for this cycle computes `earned` off zero shares.

Because a single Stacks block can contain many transactions and `current-cycle`'s `signer-rewards-per-token-for-cycle` for a `bond-index` is a single shared value that other transactions can advance (via `calculate-rewards`, `settle-rewards` triggered by unrelated stakers' unstake/registration calls), a staker calling `update-bond-registration` is exposed to losing whatever reward accrues between the settlement in their own transaction (line 896) and the removal fold (line 912-914) if any other transaction in the same or prior block advanced `rewards-per-token` for `current-signer`+`bond-index` without this staker's shares being re-settled — though within a *single* transaction, no other call typically interleaves, so the more concrete exposure is any latent inconsistency between the single settlement point and the multi-cycle removal fold, since `remove-staker-from-bond-cycles` also runs across *future* cycles (`first_reward_cycle` may be `> current_cycle`), for which **no settlement at all** is ever performed before those future-cycle balances are set to `u0` and the corresponding signer/total aggregates decremented — this is safe only as long as no rewards can accrue for a future, not-yet-started cycle, which holds under the current reward-cycle model, but the function does not enforce or assert this assumption, making it fragile to future accounting changes and inconsistent with the settle-then-mutate invariant enforced everywhere else in the file.

### Impact Explanation
This is a High-severity, minority-triggerable (unprivileged, single-account) issue matching "reward paid twice or to the wrong party" / reward mis-payment bounded to a bond participant's fee/reward class: the staker who moves signer via `update-bond-registration` can have their `current-cycle` bond reward permanently orphaned on `current-signer` (uncollectable, since `staker-shares-staked-for-cycle` for that staker/signer/cycle is zeroed without a final settle) any time `remove-staker-from-bond-for-cycle`'s implicit "no rewards-per-token change since the caller's single settlement" assumption is violated. It does not cause a chain split, but it produces reward loss for the affected caller, which is bounded to their own funds — consistent with the report's original "loss of reward" classification for `DestinationVaultMainRewarder`.

### Likelihood Explanation
Any unprivileged bond participant can trigger `update-bond-registration` at will; no majority or additional privilege is required. The realistic trigger requires the current cycle's per-bond `rewards-per-token` to have advanced between the caller's own settlement (line 896) and the later `remove-staker-from-bond-for-cycle` mutation — a narrow but reachable window if `calculate-rewards` or another staker's `settle-rewards`-inducing call for the same `bond-index` executes in between within the mempool/block-processing order (Stacks transactions in the same block are processed sequentially, so another transaction from a different sender touching the same signer/bond-index between two transactions is plausible), or, more robustly, via the always-present gap for future cycles (`first_reward_cycle > current_cycle`) where no settlement ever occurs — currently benign only because future cycles cannot have accrued rewards yet, an invariant that is not explicitly enforced in this function.

### Recommendation
Add `(settle-rewards signer reward-cycle (some bond-index))` and `(settle-staker-rewards signer reward-cycle (some bond-index) staker)` calls inside `remove-staker-from-bond-for-cycle` before zeroing `staker-shares-staked-for-cycle` and decrementing `signer-shares-staked-for-cycle`/`total-shares-staked-for-cycle`, mirroring `remove-staker-from-signer-for-cycle` and `unstake-sats-from-bond-cycle`. This makes the per-cycle settle-before-mutate invariant hold unconditionally rather than relying on an unstated assumption about future cycles never accruing rewards.

### Proof of Concept
Not directly reproducible with tools available in this session (no code execution). Conceptually:
1. Staker registers for a protocol bond under `signer1`.
2. `calculate-rewards`/sBTC transfer advances `signer-rewards-per-token-for-cycle` for `signer1`/`bond-index` at `current-cycle` (e.g., via another party's transaction in the same block, or via `calculate-rewards` executed by anyone between the staker's settlement point and the removal fold in a scenario where `update-bond-registration`'s single settlement at line 896 is stale relative to a later trigger).
3. Staker calls `update-bond-registration` to move to `signer2`. The function settles at the currently known rate (R0), then `remove-staker-from-bond-for-cycle` zeroes `staker-shares-staked-for-cycle` for `signer1` without a fresh settle.
4. If the actual settle-eligible rate advanced to R1 > R0 before the zeroing (e.g., due to unresolved reentry or multi-tx block ordering touching the same bond/cycle), the delta `(R1-R0)*shares` is unrecoverable — `staker-unclaimed-rewards-for-cycle` was only set to the R0-based `earned` and no later call re-derives it since shares are now zero.

This should be validated end-to-end in the `contrib/core-contract-tests/tests/pox-5` harness (e.g., extending `pox-5.test.ts`'s existing "does not duplicate/leak rewards" regression suite) by a background agent with test-execution access, since this session could not run the Clarinet/vitest suite to confirm the exact numeric loss.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L895-901)
```text
        ;; Settle rewards before mutating related state
        (settle-rewards current-signer current-cycle (some bond-index))
        (settle-rewards signer current-cycle (some bond-index))
        (settle-staker-rewards current-signer current-cycle (some bond-index)
            tx-sender
        )
        (settle-staker-rewards signer current-cycle (some bond-index) tx-sender)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L911-914)
```text
        ;; Remove the sBTC shares from the current signer
        (try! (remove-staker-from-bond-cycles tx-sender current-signer bond-index
            first-reward-cycle num-cycles amount-sats
        ))
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1396-1397)
```text
        (settle-rewards signer reward-cycle (some bond-index))
        (settle-staker-rewards signer reward-cycle (some bond-index) staker)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1542-1544)
```text
        ;; Settle STX-only rewards before mutating anything
        (settle-rewards signer reward-cycle none)
        (settle-staker-rewards signer reward-cycle none staker)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1887-1936)
```text
(define-private (remove-staker-from-bond-for-cycle
        (cycle-index uint)
        (accumulator-res (response {
            signer: principal,
            staker: principal,
            bond-index: uint,
            amount-sats: uint,
            first-reward-cycle: uint,
        }
            uint
        ))
    )
    (let (
            (accumulator (try! accumulator-res))
            (reward-cycle (+ cycle-index (get first-reward-cycle accumulator)))
            (signer (get signer accumulator))
            (bond-index (get bond-index accumulator))
            (amount-sats (get amount-sats accumulator))
            (current-total-staked (get-total-shares-staked-for-cycle reward-cycle (some bond-index)))
            (current-signer-staked (get-signer-shares-staked-for-cycle signer reward-cycle
                (some bond-index)
            ))
        )
        ;;  Update total shares staked for this cycle
        (map-set total-shares-staked-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
        }
            (- current-total-staked amount-sats)
        )
        ;;  Update total shares for this signer
        (map-set signer-shares-staked-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
            signer: signer,
        }
            (- current-signer-staked amount-sats)
        )
        ;;  Update staker's shares
        (map-set staker-shares-staked-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
            signer: signer,
            staker: (get staker accumulator),
        }
            u0
        )
        (ok accumulator)
    )
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2525-2530)
```text
;; Update all earned-but-unclaimed rewards for a signer, and update the snapshot
;; (signer-rewards-per-token-settled-for-cycle) for the signer.
;;
;; This MUST be called before any update to `signer-shares-staked-for-cycle`,
;; because changes to that state will effect rewards calculations.
(define-private (settle-rewards
```
