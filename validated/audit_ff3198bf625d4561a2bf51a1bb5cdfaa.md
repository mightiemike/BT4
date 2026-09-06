Found a concrete analog: `remove-staker-from-bond-for-cycle` in `stackslib/src/chainstate/stacks/boot/pox-5.clar` mutates the bond reward-accounting state (`total-shares-staked-for-cycle`, `signer-shares-staked-for-cycle`, `staker-shares-staked-for-cycle`) **without** first calling `settle-rewards`/`settle-staker-rewards`, breaking the documented invariant stated directly above `settle-rewards`: "This MUST be called before any update to `signer-shares-staked-for-cycle`, because changes to that state will effect rewards calculations."

### Title
Bond removal path skips `settle-rewards`/`settle-staker-rewards` before mutating share state, causing reward mis-accounting - (File: stackslib/src/chainstate/stacks/boot/pox-5.clar)

### Summary
`remove-staker-from-bond-for-cycle` directly mutates `total-shares-staked-for-cycle`, `signer-shares-staked-for-cycle`, and `staker-shares-staked-for-cycle` for the affected bond/cycle without first calling `settle-rewards`/`settle-staker-rewards`, unlike every sibling mutator in the same file (`add-staker-to-bond-cycles`/`add-staker-to-signer-for-cycle` at [1](#0-0) , `remove-staker-from-signer-for-cycle` at [2](#0-1) , `unstake-sats-from-bond-cycle` at [3](#0-2) , and `update-bond-registration` at [4](#0-3) ), all of which explicitly settle rewards first per the documented contract rule.

### Finding Description
The comment directly above `settle-rewards` states the required ordering: [5](#0-4)  — settlement must happen before any change to per-signer/per-staker share state, because `settle-rewards` computes `earned = shares * (rewards_per_token - rewards_per_token_settled)` using the *pre-mutation* share count (see `settle-rewards` body at [6](#0-5) ).

`remove-staker-from-bond-for-cycle`, called by `remove-staker-from-bond-cycles` (invoked from `update-bond-registration`'s "remove sBTC shares from current signer" step, [7](#0-6) ), instead reads `current-total-staked`/`current-signer-staked` and immediately writes the decremented values with **no** call to `settle-rewards`/`settle-staker-rewards`: [8](#0-7) .

This is structurally identical to the Sentiment `LEther.depositEth()`/`redeemEth()` bug: a liquidity/share-changing operation mutates the denominator used by a later interest/reward computation without first "closing the books" (`updateState()` analog is `settle-rewards`). Any subsequent `calculate-rewards` / `settle-rewards` call for that cycle+signer+bond will compute `earned` against a `signer-shares-staked-for-cycle` value that has already been decremented for this staker's removal, while `rewards-per-token-settled` was never updated to reflect the rewards accrued up to the point of removal. The staker being removed effectively forfeits (or in other flows could double count) the bond-yield interval between the last settlement and this call, and the signer's aggregate bookkeeping (`signer-rewards-per-token-for-cycle`) is now inconsistent with `signer-shares-staked-for-cycle`.

### Impact Explanation
This breaks the equality that `calculate-rewards`/`settle-rewards` are supposed to preserve: `sum(staker earned) == signer's accrued reward pool`, i.e., the reward mis-payment is a High-severity, minority-triggerable divergence bounded to bond-yield fees for the affected cycle/signer (a single unprivileged bond participant calling `update-bond-registration` can trigger it, since it invokes `remove-staker-from-bond-cycles`). It does not by itself cause a chain split, since both effects are deterministic Clarity execution replayed identically by all nodes — every node computes the same (wrong) numbers — but it is a reward-accounting bug that misallocates bond yield between the moving staker's old signer and other participants of that signer's bond pool, analogous to the "loss of yields" / "overpaid interest" impact in the source report.

### Likelihood Explanation
Triggered by any bond participant calling the public, unprivileged `update-bond-registration` entrypoint (`stackslib/src/chainstate/stacks/boot/pox-5.clar` lines 850-936) whenever their `bond-index`'s signer changes; no special privileges, majority collusion, or admin key are required.

### Recommendation
Call `settle-rewards signer reward-cycle (some bond-index)` and `settle-staker-rewards signer reward-cycle (some bond-index) staker` at the top of `remove-staker-from-bond-for-cycle`, mirroring the pattern already used in `unstake-sats-from-bond-cycle` and `remove-staker-from-signer-for-cycle`, before mutating `total-shares-staked-for-cycle`, `signer-shares-staked-for-cycle`, or `staker-shares-staked-for-cycle`.

### Proof of Concept
1. Alice registers for a protocol bond under `signer1` via `register-for-bond`, contributing `amount-sats` sBTC and joining `staker-signer-cycle-memberships` for the current and future cycles.
2. sBTC yield accrues in the pool and `calculate-rewards` is run, incrementing `signer-rewards-per-token-for-cycle` for `signer1`, but `signer-rewards-per-token-settled-for-cycle` for the bond-index is left behind Alice's un-settled portion (this is expected, pending settlement on next touch).
3. Alice calls `update-bond-registration` to move to `signer2`. This calls `remove-staker-from-bond-cycles` → `remove-staker-from-bond-for-cycle`, which reads `current-signer-staked` for `signer1`/bond-index/cycle and immediately overwrites `signer-shares-staked-for-cycle` with `(- current-signer-staked amount-sats)`, without calling `settle-rewards signer1 cycle (some bond-index)` first: [9](#0-8) .
4. Because `settle-rewards` was never invoked here, Alice's already-accrued-but-unsettled reward share for `signer1`/that cycle is silently discarded (her `staker-unclaimed-rewards-for-cycle` is never updated to reflect rewards accrued between her last settlement point and this removal), while `signer1`'s aggregate `signer-shares-staked-for-cycle` is reduced as if she had been settled. A subsequent `calculate-rewards`/`settle-rewards` call for `signer1`/cycle will therefore compute `earned` for the remaining stakers using a share total that silently dropped Alice's contribution mid-interval without crediting her the interval's yield, producing an under/over payment inconsistent with the actual sBTC held versus the intended yield formula — the same class of miscalculation as the H-6 Sentiment finding.

Note: I could not execute the Clarity test suite in this environment to numerically confirm the exact discrepancy magnitude (analogous to the Sentiment report's worked numeric example); this is a static-analysis-based finding grounded in the explicit ordering invariant documented in the source file itself, contrasted against the actual code path in `remove-staker-from-bond-for-cycle`.

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

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1693-1703)
```text
        ;; Crystallize STX-only rewards before mutating anything
        (settle-rewards signer cycle none)
        ;; When zero, this is a no-op (`earned = shares * (rpt - rpt-paid) = 0`). In this case,
        ;; we skip calling `settle-staker-rewards` to reduce cost.
        (if (> prev-staker-shares u0)
            (settle-staker-rewards signer cycle none staker)
            {
                earned: u0,
                rewards-per-token: u0,
            }
        )
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

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2525-2573)
```text
;; Update all earned-but-unclaimed rewards for a signer, and update the snapshot
;; (signer-rewards-per-token-settled-for-cycle) for the signer.
;;
;; This MUST be called before any update to `signer-shares-staked-for-cycle`,
;; because changes to that state will effect rewards calculations.
(define-private (settle-rewards
        (signer principal)
        (reward-cycle uint)
        (bond-index (optional uint))
    )
    (let (
            (shares (get-signer-shares-staked-for-cycle signer reward-cycle bond-index))
            (rewards-per-token (get-rewards-per-token-for-cycle reward-cycle bond-index))
            (earned (compute-earned-rewards
                shares
                rewards-per-token
                (get-signer-rewards-per-token-settled-for-cycle signer reward-cycle bond-index)
                (get-signer-unclaimed-rewards-for-cycle signer reward-cycle bond-index)
            ))
        )
        (map-set signer-unclaimed-rewards-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: bond-index,
            signer: signer,
        }
            earned
        )
        (map-set signer-rewards-per-token-settled-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: bond-index,
            signer: signer,
        }
            rewards-per-token
        )
        (if (> shares u0)
            (map-set signer-rewards-per-token-for-cycle {
                signer: signer,
                reward-cycle: reward-cycle,
                bond-index: bond-index,
            }
                rewards-per-token
            )
            true
        )
        {
            earned: earned,
            rewards-per-token: rewards-per-token,
        }
    )
```
