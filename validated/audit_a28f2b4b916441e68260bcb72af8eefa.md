### Title
Signer's bond-share increase skips reward settlement, letting `get-earned`/`claim-rewards` over-pay sBTC bond rewards - ([File: stackslib/src/chainstate/stacks/boot/pox-5.clar])

### Summary
`pox-5.clar` implements a Synthetix-style reward accumulator (`rewards-per-token-for-cycle`) for both the STX-only staking pool (`bond-index: none`) and per-bond sBTC pools (`bond-index: (some ...)`). The invariant that must hold before any change to a signer's staked-share balance is: settle (snapshot) the signer's pending rewards against the *old* balance, then update the balance. `add-staker-to-signer-for-cycle` (the `none`/STX path) does this correctly, but `add-staker-to-bond-for-cycle` / `remove-staker-from-bond-for-cycle` (the sBTC-bond path) do not — they mutate `signer-shares-staked-for-cycle` directly without ever calling a settle function for the signer's bond-rewards accumulator first.

### Finding Description
In the STX-only path, `add-staker-to-signer-for-cycle` explicitly settles both the signer's and the staker's pending rewards **before** touching `signer-shares-staked-for-cycle` / `staker-shares-staked-for-cycle`: [1](#0-0) 

By contrast, the bond path `add-staker-to-bond-for-cycle` mutates `total-shares-staked-for-cycle` and `signer-shares-staked-for-cycle` directly, with no call to any settle function for the signer's bond accumulator beforehand: [2](#0-1) 

The symmetric decrease path, `remove-staker-from-bond-for-cycle`, has the same omission: [3](#0-2) 

The pure accumulator math used everywhere to compute earned rewards is `earned = pending + shares * (rpt_current - rpt_paid) / PRECISION`: [4](#0-3) 

`get-earned` for a signer's bond position reads the *current* (post-mutation) `signer-shares-staked-for-cycle` together with the signer's last-settled `signer-rewards-per-token-settled-for-cycle`: [5](#0-4) 

Because `add-staker-to-bond-for-cycle` increases `signer-shares-staked-for-cycle` without first snapshotting `signer-rewards-per-token-settled-for-cycle` at the pre-increase balance, a subsequent `get-earned`/`claim-rewards` call multiplies the enlarged post-deposit share balance against the reward-per-token delta accrued since the signer's *last* settlement — a period during which the signer did not actually hold the larger balance. This is exactly the reported bug class: "parent `rewardPerToken`, but times all children's [i.e., the wrong/inflated] balance."

### Impact Explanation
Any staker who adds sBTC to an existing signer's bond position between that signer's settlement points inflates the signer's (and the staker's own, symmetrically) claimable sBTC yield beyond what was actually earned pro-rata over time. Because `calculate-rewards`/`calculate-bond-rewards` draws bond payouts from a fixed pool of accrued sBTC (`gross-accrued-rewards`, capped by `target-yield`) per `stackslib/src/chainstate/stacks/boot/pox-5.clar:2242-2337`, an inflated `get-earned` claim by one signer/staker is paid out of that shared, bounded pool at the expense of other stakers in the same reward cycle — a reward mis-payment bounded to the sBTC bond-reward pool rather than a chain split. This matches the "High - poison or reward mis-payment bounded to fees" impact tier.

### Likelihood Explanation
This is trivially and unprivilegedly triggerable: any staker who deposits into (or withdraws from) a bond position mid-cycle, via the normal bond-staking entry points that fold over `add-staker-to-bond-for-cycle`/`remove-staker-from-bond-for-cycle`, causes the miscalculation on their own signer's next `get-earned`/`claim-rewards` call — no majority, no admin key, and no cross-node disagreement required (the bug is deterministic and reproducible by any node evaluating the same contract calls).

### Recommendation
Before mutating `signer-shares-staked-for-cycle` / `staker-shares-staked-for-cycle` in `add-staker-to-bond-for-cycle` and `remove-staker-from-bond-for-cycle`, call the equivalent of `settle-rewards`/`settle-staker-rewards` for the `(some bond-index)` case (analogous to what `add-staker-to-signer-for-cycle` already does for `none`), so that pending earnings are snapshotted against the pre-mutation balance before the balance changes.

### Proof of Concept
1. Signer `S` has an existing bond position at `bond-index B` for `reward-cycle C` with `signer-shares-staked-for-cycle = X` sats, last settled at `rpt_paid = P0`.
2. Between settlements, `calculate-rewards` runs and bumps `rewards-per-token-for-cycle(C, B)` to `P1 > P0` (accrued interest on the existing `X` sats).
3. A staker calls the bond-stacking entry point which invokes `add-staker-to-bond-for-cycle`, adding `Y` sats to signer `S`'s bond share — `signer-shares-staked-for-cycle` becomes `X + Y`, with **no settlement call**, so `signer-rewards-per-token-settled-for-cycle` for `S` remains `P0`.
4. Signer `S` calls `get-earned`/`claim-rewards`: `earned = pending + (X + Y) * (P1 - P0) / PRECISION`, per `compute-earned-rewards` at `stackslib/src/chainstate/stacks/boot/pox-5.clar:2378-2385`, instead of the correct `X * (P1 - P0) / PRECISION` (since `Y` was not staked during the `P0→P1` accrual window). The signer is over-paid by `Y * (P1 - P0) / PRECISION`, drawn from the shared bond reward pool.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1692-1712)
```text
        )
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

        (if (>= new-delegated SIGNER_SET_MIN_USTX)
            (begin
                (map-set signer-shares-staked-for-cycle {
                    reward-cycle: cycle,
                    bond-index: none,
                    signer: signer,
                }
                    (+ prev-staked stake-amount)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1806-1865)
```text
(define-private (add-staker-to-bond-for-cycle
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
        ;; Update total shares staked for this cycle
        (map-set total-shares-staked-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
        }
            (+ current-total-staked amount-sats)
        )
        ;; Update total shares for this signer
        (map-set signer-shares-staked-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
            signer: signer,
        }
            (+ current-signer-staked amount-sats)
        )
        ;; Update staker's shares
        (map-set staker-shares-staked-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
            signer: signer,
            staker: (get staker accumulator),
        }
            amount-sats
        )
        ;; Mark settled rewards for this cycle
        (map-set staker-rewards-per-token-settled-for-cycle {
            reward-cycle: reward-cycle,
            bond-index: (some bond-index),
            signer: signer,
            staker: (get staker accumulator),
        }
            (get-signer-rewards-per-token-for-cycle signer reward-cycle
                (some bond-index)
            ))
        (ok accumulator)
    )
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

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2341-2354)
```text
(define-read-only (get-earned
        (signer principal)
        (reward-cycle uint)
        (bond-index (optional uint))
    )
    (compute-earned-rewards
        (get-signer-shares-staked-for-cycle signer reward-cycle bond-index)
        (get-rewards-per-token-for-cycle reward-cycle bond-index)
        (get-signer-rewards-per-token-settled-for-cycle signer reward-cycle
            bond-index
        )
        (get-signer-unclaimed-rewards-for-cycle signer reward-cycle bond-index)
    )
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2375-2385)
```text
;; Pure math formula for computing rewards earned since the last snapshot
;;
;; `earned = (shares * (rpt - rptPaid)) / PRECISION + pending`
(define-read-only (compute-earned-rewards
        (shares uint)
        (rpt-current uint)
        (rpt-paid uint)
        (pending uint)
    )
    (+ pending (/ (* shares (- rpt-current rpt-paid)) PRECISION))
)
```
