### Title
Protocol treasury fee (`reserve-inc`/`treasury-lp`) permanently truncates to zero on small interest deltas due to integer division precision loss - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
In every vault contract (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`), the `accrue()` function computes the protocol's reserve fee share as `reserve-inc = mul-div-down debt-delta fee-reserve BPS`. Because `debt-delta` is measured in the underlying asset's native decimals (e.g. 6 for USDC) and `BPS = u10000`, any call to `accrue()` where the just-accrued interest increment is small relative to `BPS/fee-reserve` causes `reserve-inc` (and consequently `treasury-lp`) to round down to `0`. Since `accrue()` is called on essentially every state-changing operation (deposit, borrow, repay, redeem) and there is no accumulator that carries the truncated remainder forward, each such call permanently discards the protocol's fee share for that period — this is the same root cause as Sherlock M-22 in Derby's `storePriceAndRewards()`, where `nominator/denominator` rounded to zero and eliminated the reward for the period entirely.

### Finding Description
`accrue()` in the vault contracts (e.g. `mainnet/contracts/vault/v0-vault-usdc.clar`) computes: [1](#0-0) 

```clarity
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          (ok { index: idx, lindex: lidx })
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            ...
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            ...
            (ok { index: next, lindex: nliq })))))
```

`debt-delta` is the incremental compound-interest accrued between the previous stored `index` and the freshly computed `next` index for the current call (`mul-div-down scaled-principal idx INDEX-PRECISION` vs `... next ...`), computed in the underlying asset's raw decimals (e.g. `u6` for USDC per `DECIMALS`, `BPS = u10000` per [2](#0-1) ).

`reserve-inc = debt-delta * fee-reserve / BPS`. Because Clarity integer division truncates, if `debt-delta * fee-reserve < BPS` (10000), `reserve-inc` evaluates to exactly `0`, and consequently `treasury-lp` is forced to `0` and no `ft-mint?` to `.dao-treasury` occurs for that call. The stored `index`/`lindex` still advance (borrowers/depositors get their full economic effect), but the "value bound" that is supposed to compensate the protocol treasury for that increment (`reserve-inc`) is computed, checked, and discarded within the same call/transaction with no remainder carried to the next `accrue()` invocation — the next call recomputes `debt-delta` from the now-updated `idx`, so the truncated fraction is not recoverable. Since `accrue()` is invoked as a precondition inside `system-borrow`, and analogous deposit/redeem/repay paths, it is called very frequently (effectively on every user transaction), meaning `time-delta` since `last-update` is typically small, keeping `debt-delta` small and precision loss recurring on virtually every call for low-decimal underlyings like USDC (6 decimals) or low `fee-reserve` settings.

This is the direct Zest analog of the reported Derby bug: a per-call/per-period reward-like value (`nominator/denominator` there, `reserve-inc = debt-delta*fee-reserve/BPS` here) is computed with a numerator that is systematically too small relative to the denominator scale for low-decimal assets, causing the computed value to floor to zero on essentially every invocation and to be permanently lost since no accumulator/remainder mechanism exists.

### Impact Explanation
The protocol's `dao-treasury` unclaimed reserve-fee yield (`treasury-lp`, minted as `zft` shares) is silently and permanently forfeited whenever `debt-delta * fee-reserve < BPS` within an `accrue()` call. Because `accrue()` runs on nearly every user-facing action, and the failure mode recurs at every call rather than being caught up later, the protocol can lose all or nearly all of its intended reserve-fee revenue for lower-decimal underlying assets (USDC/USDH, 6 decimals) or for any vault where borrow activity/time deltas between accruals are small. This constitutes permanent theft/freezing of unclaimed protocol yield (the treasury's fee-reserve share), which is a valid High-severity impact per the specified impact classes.

### Likelihood Explanation
`fee-reserve` is configurable (BPS-denominated, up to `u10000`), and `accrue()` is triggered on nearly every write operation (borrow, repay, deposit, redeem), so `time-delta` and thus `debt-delta` between successive `accrue()` calls will frequently be small in absolute underlying units, especially for low-decimal assets like USDC/USDH or during periods of light protocol usage/low utilization. No special attacker action is required — this occurs under ordinary operation whenever interest per call is small relative to `BPS`, making the likelihood high for USDC/USDH vaults and any low-activity vault.

### Recommendation
Introduce higher internal precision for the intermediate `reserve-inc`/`treasury-lp` calculation (e.g., scale `debt-delta` up by a fixed `BASE_SCALE` before multiplying by `fee-reserve`, then divide down by `BASE_SCALE` only at the point of minting), or accumulate the truncated remainder in a persistent data-var so it is carried forward and applied once significant enough to mint, rather than discarding it on every `accrue()` call where it rounds to zero.

### Proof of Concept
1. Deploy `v0-vault-usdc.clar` with `fee-reserve` set to, e.g., `u1000` (10%) and any positive interest rate curve.
2. User calls a public entrypoint that triggers `accrue()` (e.g. `system-borrow`) shortly (small `time-delta`) after the previous accrual, such that `debt-delta` (interest accrued in raw USDC units, 6 decimals) computed via `old-debt`/`new-debt` is, say, `9` (any value where `9 * 1000 / 10000 = 0` under integer division).
3. `reserve-inc = mul-div-down(9, 1000, 10000) = 0`, so `treasury-lp = 0`, and the `if (> treasury-lp u0) (try! (ft-mint? zft treasury-lp .dao-treasury))` branch is skipped — no `zft` is minted to `.dao-treasury` for this period. [3](#0-2) 
4. Because `index`/`lindex` still advance based on `next`/`nliq`, the next call's `old-debt` starts from the already-updated `idx`, so the value of `debt-delta` that rounded to zero is never recomputed or recovered in any subsequent call.
5. Repeat step 2 across many small, frequent accruals (normal usage pattern for a real vault) to show the treasury's fee-reserve share is cumulatively and permanently lost.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L20-28)
```text
(define-constant NAME "Zest USDC")
(define-constant SYMBOL "zUSDC")
(define-constant DECIMALS u6)

;; -- Precision & scaling
(define-constant BPS u10000)
(define-constant PRECISION u100000000)
(define-constant INDEX-PRECISION u1000000000000)  ;; 1e12 for index calculations
(define-constant SECONDS-PER-YEAR-BPS (* u31536000 BPS))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L833-865)
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
  (let (
      (states (var-get pause-states))
```
