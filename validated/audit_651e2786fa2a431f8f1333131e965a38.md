### Title
Stale `last-update` timestamp lets a DAO interest-rate-curve change retroactively misprice the idle period - (File: `mainnet/contracts/vault/v0-vault-stx.clar`)

### Summary
`last-update` in every Zest vault only advances when the computed index actually changes. At zero utilization the base borrow rate for several vaults (STX, USDC, USDH) is `u0`, so `next-index`/`next-liquidity-index` return the unchanged index and `last-update` is left frozen indefinitely. If governance then raises the rate curve via `set-points-rate`/`set-points-util` while the vault is still idle, the next real `accrue()` call applies the *new* rate retroactively over the entire frozen interval, fabricating interest that was never actually owed for that period.

### Finding Description
`next-index` and `next-liquidity-index` compute a time-delta against `last-update` and update `last-update` only if the index changed: [1](#0-0) 

`interest-rate` reads the current `points-ir` variable and interpolates against current `utilization`: [2](#0-1) 

When utilization is `0`, the STX/USDC/USDH curves return a `0` base rate (`RATE-POINTS-STX` starts with `u0`, etc.), so `calc-multiplier-delta` yields no growth, `next == idx`, `nliq == lidx`, and the guard that advances the clock is skipped: [3](#0-2) 

`last-update` therefore stays pinned at whatever timestamp the vault was last non-idle, for as long as the vault remains at 0% utilization — this is the "clock advanced only on change" pattern.

Governance can change the rate curve at any time via `set-points-rate`/`set-points-util`, which call `accrue()` *before* writing the new curve, but `accrue()` — being at the same frozen, zero-rate state — again computes `next == idx` and does not touch `last-update`: [4](#0-3) 

Unlike the pause/unpause path, which explicitly jumps `last-update` forward to skip the paused interval (`(var-set last-update stacks-block-time)` on unpause), `set-points-rate`/`set-points-util` have no equivalent "catch-up" of the clock: [5](#0-4) 

After the curve is updated to a non-zero base rate, the next time anyone triggers `accrue()` (via `deposit`, `redeem`, `system-borrow`, `system-repay`, etc.) the `time-delta = stacks-block-time - last-update` still spans the entire frozen, previously-idle interval, and `interest-rate()` now reads the *new* curve. The result is that interest accrues, using the new rate, over a period during which the old (zero) rate should have applied and nothing was owed.

### Impact Explanation
This mints phantom debt: existing borrowers (if utilization becomes non-zero right after the curve change) or the vault's `total-borrowed`/treasury-fee accounting are charged/credited interest for a period during which no interest should have accrued. Because the DAO fully controls `set-points-rate`, this can be triggered without any attacker action, and it silently misprices interest that has already legitimately elapsed — the funds this manufactures are transferred from borrowers to the reserve/LP pool via `treasury-lp` mint and `index` growth. This is a form of theft of unclaimed yield (borrowers overpay interest that flows to LPs/treasury for a period where it was not economically earned), matching the "theft of unclaimed yield" High-impact category.

### Likelihood Explanation
Reachable purely through normal governance operation, no exploit or malicious actor is required — any DAO proposal that raises `points-rate`/`points-util` for a currently idle (0% utilization) vault triggers it, and idle periods (0% utilization) are common for less-active vaults (e.g. `vault-usdh`, `vault-ststxbtc`). The longer the vault sits idle before the rate change, the larger the mispriced interval.

### Recommendation
When `set-points-util`/`set-points-rate` are invoked, force `last-update` to `stacks-block-time` after `accrue()` (mirroring the unpause logic in `set-pause-states`) so that no elapsed time is left unaccounted-for under a rate curve that didn't exist during that interval.

### Proof of Concept
1. Vault (e.g. `vault-usdh`) sits at 0% utilization; `interest-rate()` returns `u0` (`RATE-POINTS-USDH` starts with `u0`), so every `accrue()` call leaves `index`/`lindex` unchanged and `last-update` frozen at time `T0`.
2. Time passes to `T1` (e.g. weeks later), utilization is still `0%`.
3. DAO executes a proposal calling `vault-usdh.set-points-rate` with a new curve whose base (0%-utilization) rate is non-zero. `accrue()` runs first at `T1` under the OLD (0%) curve — no change, `last-update` still `T0`. The new curve is then written.
4. Immediately after, a user deposits/borrows, calling `accrue()` again at `T1+ε`. `time-delta = (T1+ε) - T0` (the entire idle interval), and `interest-rate()` now reads the NEW non-zero curve.
5. `index`/`lindex` jump to reflect interest accrued at the new rate over the whole `T0→T1` interval, even though the old 0% rate legitimately applied for that entire span — manufacturing debt/yield that was never actually owed. [6](#0-5)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L371-377)
```text
(define-private (interest-rate)
  (let ((points-data (var-get points-ir))
        (uword (get util points-data))
        (rword (get rate points-data))
        (utils (unpack-u16 uword))
        (rates (unpack-u16 rword)))
    (interpolate-rate (utilization) utils rates)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L379-404)
```text
(define-private (next-index)
  (let ((states (var-get pause-states))
        (idx (var-get index)))
    (if (get accrue states)
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))

(define-private (next-liquidity-index)
  (let ((states (var-get pause-states))
        (lidx (var-get lindex)))
    (if (get accrue states)
        lidx
        (let (
            (rate (interest-rate))
            (liquidity-rate (calc-liquidity-rate rate (utilization) (var-get fee-reserve)))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta liquidity-rate time-delta false))))
          (calc-index-next lidx multiplier)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L666-702)
```text
(define-public (set-points-util (points (list 8 uint)))
    (let (
          (packed (unwrap-panic (pack-u16 points (some BPS))))
          (pir (var-get points-ir)))
      (try! (check-dao-auth))
      (try! (accrue))
      (var-set points-ir { util: packed, rate: (get rate pir) })
      
      (print {
        action: "vault-set-points-util",
        caller: tx-sender,
        data: {
          vault: UNDERLYING,
          points: points
        }
      })
      
      (ok true)))

(define-public (set-points-rate (points (list 8 uint)))
    (let (
          (packed (unwrap-panic (pack-u16 points none)))
          (pir (var-get points-ir)))
      (try! (check-dao-auth))
      (try! (accrue))
      (var-set points-ir { util: (get util pir), rate: packed })
      
      (print {
        action: "vault-set-points-rate",
        caller: tx-sender,
        data: {
          vault: UNDERLYING,
          points: points
        }
      })
      
      (ok true)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-865)
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

(define-public (system-borrow (amount uint) (receiver principal))
```

**File:** local-testing/contracts/vault/vault-ststx.clar (L727-741)
```text
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
```
