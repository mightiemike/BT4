### Title
`socialize-debt` Writes Down `lindex` Using Stale Cached Interest State Instead of Accruing First - (File: `mainnet/contracts/vault/v0-vault-stx.clar`, function `socialize-debt`)

### Summary
`socialize-debt` reads the vault's interest-accrual state (`index`, `lindex`, `assets`, `total-assets`) directly from cached data-vars and mutates `lindex` without first calling `accrue`, unlike every other state-mutating vault entry point (`system-borrow`, `system-repay`, `deposit`, `redeem`), which all begin with `(try! (accrue))`. This lets pending, un-materialized interest be silently absorbed or duplicated when the write-down is later reconciled by a subsequent `accrue` call, permanently corrupting the liquidity index used for share pricing.

### Finding Description
Every economically significant vault entry point syncs cached interest state before mutating it, e.g. `system-borrow`: [1](#0-0) 
and `system-repay`: [2](#0-1) 
Both begin with `(u (try! (accrue)))`, which advances `index`/`lindex` to reflect all interest accrued since `last-update` before any other computation is performed, per the `accrue` implementation: [3](#0-2) 

`socialize-debt`, however, reads `index`, `lindex`, `assets`, and `total-assets` straight from their cached data-vars, with no call to `accrue`, before computing and permanently writing down `lindex`: [4](#0-3) 

Because `idx`/`lindex`/`assets`/`total-assets` are caches that are only invalidated by `accrue` (which updates them and advances `last-update`), any interest that accrued between the last `accrue` call and the `socialize-debt` transaction remains "in flight." `socialize-debt` computes `debt-reduction` and the write-down ratio (`new-lindex`) against these stale, pre-accrual numbers and then unconditionally overwrites `lindex` via `var-set lindex new-lindex` — without updating `last-update`. On the *next* transaction that calls `accrue` (e.g. the following `deposit`/`borrow`/`repay`), `next-liquidity-index` computes a fresh multiplier using `(- stacks-block-time (var-get last-update))` as the time delta and applies it on top of the just-reduced `lindex`: [5](#0-4) 
This means the interest that accrued *before* `socialize-debt` executed — which should have been reflected in `old-total-assets`/`debt-reduction` at write-down time — instead gets applied a second time, now compounding on the artificially-reduced `lindex`. The write-down ratio itself is therefore computed from an under-stated debt/asset snapshot, mispricing the socialized loss relative to suppliers' shares.

### Impact Explanation
`lindex` directly determines the redemption value of every `zft` share via `convert-to-assets-preview`. A mispriced, permanently-cached `lindex` write-down skews future redemptions for all liquidity providers — some depositors will be able to redeem against a liquidity index that does not correctly reflect the true post-write-off asset backing, while others are shortchanged. This is a permanent, protocol-wide corruption of share pricing tied to the debt-socialization (bad-debt write-off) path, which qualifies as protocol insolvency risk / permanent freezing of funds for depositors whose share value is miscalculated going forward.

### Likelihood Explanation
`socialize-debt` is gated by `check-caller-auth`, restricting it to other authorized in-protocol contracts (e.g., a liquidation/market contract), not arbitrary users: [6](#0-5) 
The trigger condition is simply that some time (any positive `time-delta`, i.e. any block/time elapsed since the last `accrue`) has passed before `socialize-debt` is invoked without an intervening `accrue` — a routine condition in normal protocol operation whenever bad debt needs socializing, since nothing in the code path forces `accrue` to run immediately beforehand.

### Recommendation
Call `(try! (accrue))` at the start of `socialize-debt`, exactly as done in `system-borrow`/`system-repay`/`deposit`/`redeem`, so that `index`, `lindex`, and `last-update` are synchronized to the current block before computing the debt write-down, and compute `debt-reduction`/`new-lindex` against the freshly-accrued values rather than stale cached ones.

### Proof of Concept
1. Vault accrues interest normally; `last-update` = T0, `index`/`lindex` reflect state at T0.
2. Time passes (or blocks advance) without any `deposit`/`redeem`/`borrow`/`repay` call — no `accrue` occurs, so `index`/`lindex`/`assets` remain frozen at T0 values, though `interest-rate() * time-delta` interest has economically accrued.
3. An authorized contract calls `socialize-debt` at time T1 (T1 > T0) to write off bad debt. `socialize-debt` uses the stale `idx`, `current-lindex`, and `old-total-assets` (all as of T0) to compute `debt-reduction` and `new-lindex`, then `var-set lindex new-lindex` — permanently committing a write-down based on outdated numbers, and `last-update` remains T0.
4. A subsequent call to any function that invokes `accrue` (e.g. `deposit`) computes `time-delta = T2 - T0` (which still includes the T0→T1 interval already "baked into" data that socialize-debt should have reconciled) and applies the accrued-interest multiplier on top of the already-reduced `lindex`, effectively double-applying interest that should have been captured before the write-down.
5. Result: `lindex` used for all future `convert-to-assets-preview` calls is inconsistent with the true post-write-off backing, corrupting redemption values for all `zft` holders going forward.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L863-877)
```text
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
      (debt (total-debt))
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
      (updated-scaled-principal (+ scaled-principal scaled-amount)))

    (try! (check-caller-auth))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L902-920)
```text
(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
        (total-borrowed-amount (var-get total-borrowed))
        (capped-amount (if (> amount debt) debt amount))
        (principal-reduction (calc-principal-ratio-reduction capped-amount scaled-principal debt))
        (capped-reduction (if (> principal-reduction scaled-principal) scaled-principal principal-reduction))
        (updated-scaled-principal (- scaled-principal capped-reduction))
        (principal-repaid (mul-div-down capped-amount total-borrowed-amount debt))
        (interest-paid (- capped-amount principal-repaid))
        (total-borrowed-new (if (> total-borrowed-amount principal-repaid) (- total-borrowed-amount principal-repaid) u0)))

    (try! (check-caller-auth))
    (asserts! (not (get repay states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-967)
```text
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        (current-assets (var-get assets))
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

```

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L391-402)
```text
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

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L833-863)
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
