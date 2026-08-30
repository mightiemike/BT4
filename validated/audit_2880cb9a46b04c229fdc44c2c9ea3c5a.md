### Title
Interest-accrual timestamp not advanced when index is momentarily unchanged, allowing stale `last-update` to retroactively apply a new rate over a dead period - (File: mainnet/contracts/vault/v0-vault-sbtc.clar)

### Summary
`accrue()` only updates `last-update` when the freshly-computed borrow/liquidity index actually differs from the stored one. When utilization (and therefore the borrow rate) is zero, the computed `next-index` is mathematically identical to the current `index`, so `last-update` is left stale even though real time has elapsed. When utilization later becomes non-zero, the next `accrue()` call measures the time-delta from that stale `last-update`, and applies the *new*, non-zero rate across the entire elapsed window — including the period when the rate was actually zero.

### Finding Description
In `accrue()`:
```
(if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
    (var-set last-update stacks-block-time)
    false)
``` [1](#0-0) 

`last-update` (the value that anchors `time-delta` in `calc-multiplier-delta`) is only advanced when `index`/`lindex` change. Because `next-index` is derived from `current-rate`, and the interest-rate curve returns `u0` at zero utilization, `next-index` equals `idx` bit-for-bit whenever the market is idle (no debt outstanding). The clock ("last-update") is therefore only advanced on change, exactly the "clock advanced only on change" analog: the cached timestamp is not invalidated by the passage of time itself, only by a resulting index change, and a later mutation (rate turning non-zero) uses that stale clock.

Sequence:
1. Vault has zero debt → `calc-utilization` returns `u0` → `current-rate` is `u0` → `calc-multiplier-delta` returns exactly `INDEX-PRECISION` → `next-index == idx`, so `last-update` is not written, staying at an old timestamp `T0`.
2. Time passes (the idle period can be arbitrarily long) with the rate legitimately at zero — this is correct behavior while it lasts.
3. A borrow occurs (`system-borrow`), moving utilization above zero and making `current-rate` non-zero going forward.
4. On the very next `accrue()` call at time `T2`, `calc-multiplier-delta` computes `time-delta = T2 - last-update = T2 - T0`, i.e. the *entire* idle-plus-active window, and applies the *current* non-zero rate to that whole span instead of only to the time since the rate actually became non-zero.

This silently inflates `debt-delta`, which inflates `reserve-inc` and the `treasury-lp` minted to `.dao-treasury`, and inflates every borrower's `total-debt` via the compounding index — attributing interest for a period during which the true rate was zero.

### Impact Explanation
This falls under theft/misappropriation of unclaimed yield: excess `zft` treasury shares are minted to `.dao-treasury` based on interest that was never actually owed, diluting existing depositors' claim on the vault, and outstanding borrowers are charged interest for a period when the rate should have been zero. Because this happens automatically from normal usage of a single vault's accrual mechanism (no privileged action, no DAO compromise required), it is an in-scope, reachable single-vault/single-timeline defect (not user-vs-user interference).

### Likelihood Explanation
Any of the six vaults (`v0-vault-stx`, `v0-vault-sbtc`, `v0-vault-ststx`, `v0-vault-usdc`, `v0-vault-usdh`, `v0-vault-ststxbtc`) that goes through a natural idle period with zero outstanding debt before a new borrow is issued will trigger this path deterministically; no attacker coordination or edge-case timing is needed beyond normal utilization dropping to zero, which is a routine market state.

### Recommendation
Decouple `last-update` from index-change detection: always set `last-update` to `stacks-block-time` on every `accrue()` invocation (or explicitly track the time-delta independent of whether `next-index` differs from `idx`), so a zero-rate period is correctly excluded from future interest calculations regardless of whether the index bit-pattern changed.

### Proof of Concept
1. Deploy vault with zero outstanding debt; utilization = 0 → `current-rate` = 0.
2. Wait N blocks/time without any borrow — `accrue()` calls during this window compute `next == idx`, so `last-update` stays at `T0`.
3. Call `system-borrow` to create debt, moving utilization > 0.
4. Call `accrue()` at time `T2`; observe `time-delta` used in `calc-multiplier-delta` equals `T2 - T0` (full idle+active span) rather than the correct shorter interval since the rate became non-zero, inflating `debt-delta`/`treasury-lp` and every borrower's compounded debt. [1](#0-0) [2](#0-1)

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L833-861)
```text
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L164-184)
```text
(define-private (calc-utilization (available-liquidity uint) (debt-amount uint))
  (let ((total (+ debt-amount available-liquidity)))
    (if (is-eq total u0)
        u0
        (mul-div-down debt-amount BPS total))))

(define-private (calc-multiplier-delta (rate uint) (time-delta uint) (round-up bool))
  (+ INDEX-PRECISION
    (if round-up
      (mul-div-up rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS)
      (mul-div-down rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS))))

(define-private (calc-cumulative-debt (principal-amount uint) (idx uint))
  (mul-div-up principal-amount idx INDEX-PRECISION))

(define-private (calc-index-next (index-curr uint) (multiplier uint))
  (mul-div-down index-curr multiplier INDEX-PRECISION))
```
