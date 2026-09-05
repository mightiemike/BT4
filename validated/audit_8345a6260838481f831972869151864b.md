### Title
Reward mis-payment: `calculate-rewards` distributes STX-staker rewards by point-in-time share snapshot, letting a last-minute staker capture yield accrued before they staked - ([File: stackslib/src/chainstate/stacks/boot/pox-5.clar])

### Summary
`pox-5.clar`'s STX-only staking reward accounting divides all sBTC that arrived since the last distribution (`gross-accrued-rewards`/`get-new-rewards`) by the *current* total staked shares at the moment `calculate-rewards` executes, rather than by a time-weighted measure of who actually held stake while those rewards accrued. Any unprivileged account can call `stake` at any point before the distribution height (outside the short prepare-phase window) and immediately be included in `cycle-staked-ustx` when `calculate-rewards` is next called by anyone, diluting the rightful yield of stakers who were locked in for the entire period. This mirrors the Reserve Protocol StRSR finding: rewards are paid out lazily/in bulk instead of continuously per-staker, so newcomers can free-ride on rewards that accrued before they joined.

### Finding Description
`get-new-rewards` computes the sBTC that has accumulated in the contract since the last `calculate-rewards` call: [1](#0-0) 

`calculate-rewards` then divides that lump sum of `stx-staker-rewards` by `cycle-staked-ustx`, which is read as the *current* value of `total-shares-staked-for-cycle` at the moment of the call — not a time-weighted average of shares held throughout the accrual window: [2](#0-1) 

Meanwhile, `stake` (via `add-staker-to-signer-for-cycle`) is permitted to add a new staker's shares to `staker-shares-staked-for-cycle` / `total-shares-staked-for-cycle` for the current cycle right up until the (short) prepare phase gate, and the `settle-rewards` call it performs before mutating state only crystallizes the *existing* signer's/staker's already-settled snapshot — it does nothing to prevent the newcomer from being counted in the very next `calculate-rewards` distribution: [3](#0-2) 

Because `rewards-per-token-for-cycle` for the `none` (STX-only) pool is a single global accumulator per cycle (not accrued continuously per block/share-second), a staker who joins immediately before `calculate-rewards` fires receives the exact same `rewards-per-token` credit as a staker who has been locked for the whole cycle: [4](#0-3) 

This is the same root cause as the referenced StRSR finding: rewards are settled in a lump-sum, discrete "epoch" fashion, and the contract has no mechanism to prevent late joiners from participating in a distribution whose underlying yield accrued before they added their stake. `calculate-rewards` itself carries no admin/authorization check — it is callable by anyone once the calculation height has passed — so the attack requires no privileged party, and the staking window is open to any unprivileged account except during the brief prepare phase.

### Impact Explanation
This breaks the equality "a staker's share of distributed rewards should be proportional to their time-weighted stake during the accrual window." Instead, rewards are mis-paid to whoever holds shares at the exact moment `calculate-rewards` executes, diluting long-term stakers' rightful yield and letting the newcomer capture value with a near-zero holding period. The magnitude is bounded by the sBTC reward pool accrued since the last distribution (analogous to "reward mis-payment bounded to fees" in the High severity bucket) — it does not cause a chain split, an invalid block, or double-spend of the principal stake, but it is a concrete, unprivileged reward-mispayment/theft-of-yield bug reachable by any single staker.

### Likelihood Explanation
Likelihood depends on the cadence of `calculate-rewards` calls relative to sBTC reward inflows, analogous to the original report's dependency on `rewardPeriod`/pool liveliness. Since `calculate-rewards` is permissionless and can be triggered by the attacker themselves the instant the calculation height is reached, and `stake` is blocked only during the short prepare-phase window (not throughout the whole cycle/half-cycle), an attacker can reliably time a stake just before a pending distribution and immediately trigger (or wait for someone else to trigger) `calculate-rewards`, making exploitation straightforward and repeatable every half-cycle.

### Recommendation
Adopt a continuously-accruing (streaming) rewards-per-token model, or gate new stakers' inclusion in a cycle's `total-shares-staked-for-cycle` so they only participate in rewards accrued *after* they join (e.g., snapshot `rewards-per-token-for-cycle` for the newcomer at their settled/paid checkpoint at the moment of joining, as is already done for signers/stakers in `settle-rewards`/`settle-staker-rewards`, but applied consistently so that `calculate-rewards`'s lump-sum distribution cannot retroactively include shares added after the reward-accrual window began). Alternatively, require `calculate-rewards` to use a time-weighted average of shares over the elapsed accrual window rather than a point-in-time snapshot.

### Proof of Concept
Conceptual sequence (mirrors the referenced StRSR PoC), based on the code paths cited above:
1. sBTC rewards accrue in the `pox-5` contract over a half-cycle (e.g., via `sbtc-token transfer` to the contract), tracked implicitly by `get-new-rewards` (no `calculate-rewards` has been called yet during this window).
2. Immediately before the block at which `calculate-rewards` becomes callable (`calculation-height`), attacker calls `stake` (permitted, since this is outside the prepare-phase gate), adding shares to `total-shares-staked-for-cycle` for the current STX-only cycle via `add-staker-to-signer-for-cycle`.
3. Anyone (including the attacker) calls `calculate-rewards`. `cycle-staked-ustx` now includes the attacker's freshly added stake; `accrued-rewards-per-ustx` is computed by dividing the full accrued `stx-staker-rewards` by this inflated total.
4. The attacker's `staker-shares-staked-for-cycle` entry, credited at the new `rewards-per-token-for-cycle`, entitles them to a full pro-rata share of rewards that accrued almost entirely before they staked, diluting the long-term stakers' `getEarnedStakerRewards`.

I was not able to directly view the `stake` public function body or the exact relationship between `HALF_CYCLE_LENGTH`/`PREPARE_CYCLE_LENGTH` and the prepare-phase gate due to running out of search iterations; this should be confirmed by a follow-up review of `stake`'s prepare-phase check and the distribution-cycle length constants in `stackslib/src/chainstate/stacks/boot/pox-5.clar` before treating this as fully confirmed.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1663-1710)
```text
(define-private (add-staker-to-signer-for-cycle
        (cycle-index uint)
        (accumulator-res (response {
            signer: principal,
            staker: principal,
            amount-ustx: uint,
            first-reward-cycle: uint,
            is-stx-staking: bool,
        }
            uint
        ))
    )
    (let (
            (accumulator (try! accumulator-res))
            (cycle (+ cycle-index (get first-reward-cycle accumulator)))
            (signer (get signer accumulator))
            ;; Get the total uSTX delegated (through protocol bonds and STX-only
            ;; staking) to this signer.
            (cur-delegated-for-signer (get-amount-delegated-for-signer signer cycle))
            (amount (get amount-ustx accumulator))
            (stake-amount (if (get is-stx-staking accumulator)
                amount
                u0
            ))
            (staker (get staker accumulator))
            (prev-staked (get-signer-pending-staked-ustx-per-cycle signer cycle))
            (prev-total-shares-staked (get-total-shares-staked-for-cycle cycle none))
            (new-delegated (+ cur-delegated-for-signer amount))
            (prev-staker-shares (get-staker-shares-staked-for-cycle staker cycle none signer))
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
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2147-2156)
```text
;; Returns the total amount of newly received sBTC rewards
;; since the last rewards computation
(define-read-only (get-new-rewards)
    (let (
            (last-accounted-rewards (var-get last-accounted-rewards-only))
            (rewards-balance (get-rewards))
        )
        (- rewards-balance last-accounted-rewards)
    )
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2192-2221)
```text
                (cycle-staked-ustx (get-total-shares-staked-for-cycle stx-cycle none))
                (current-rewards-per-ustx (get-rewards-per-token-for-cycle stx-cycle none))
                (prev-accounted-rewards (var-get last-accounted-rewards-only))
                ;; If no STX is staked this cycle, the staker cut will be applied to the reserve.
                (no-stx-stakers (is-eq cycle-staked-ustx u0))
                (accrued-rewards-per-ustx (if no-stx-stakers
                    u0
                    (/ (* stx-staker-rewards PRECISION) cycle-staked-ustx)
                ))
                (cumulative-rewards-per-ustx (+ current-rewards-per-ustx accrued-rewards-per-ustx))
                ;; When no STX is staked, fold the staker cut into the reserve, otherwise zero.
                (unallocated-staker-cut (if no-stx-stakers
                    stx-staker-rewards
                    u0
                ))
                (reserve-deposit (+ reserve-cut unallocated-staker-cut))
                (new-reserve-balance (+ cur-reserve reserve-deposit))
            )
            (var-set reserve-balance new-reserve-balance)
            (var-set last-reward-compute-height calculation-height)
            (var-set last-accounted-rewards-only
                (+ prev-accounted-rewards
                    (- gross-accrued-rewards reserve-deposit)
                ))
            (map-set rewards-per-token-for-cycle {
                reward-cycle: stx-cycle,
                bond-index: none,
            }
                cumulative-rewards-per-ustx
            )
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2530-2574)
```text
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
)
```
