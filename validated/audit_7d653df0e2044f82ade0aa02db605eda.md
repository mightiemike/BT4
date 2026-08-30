### Title
Unbounded `fee-reserve` combined with large `debt-delta` causes underflow panic in `accrue`, bricking all vault operations - (File: `mainnet/contracts/vault/v0-vault-stx.clar`)

### Summary
`set-fee-reserve` only bounds the fee below `BPS` (i.e. up to just under 100%), with no upper cap tied to the actual asset base the fee is taken from. `accrue()` uses this fee to compute `reserve-inc` from `debt-delta`, then subtracts `reserve-inc` from `(total-assets-preview)` to size the treasury's LP mint. If `reserve-inc` ever reaches or exceeds `total-assets-preview`, the subtraction underflows and Clarity aborts the transaction — and since every deposit, redeem, borrow, and repay entrypoint calls `(try! (accrue))` first, this bricks the entire vault.

### Finding Description
`set-fee-reserve` validates only: [1](#0-0) 

There is no `maxLicenseFee`-style ceiling relative to vault size or accrued debt — the same missing-cap defect described in the source report, just re-implemented for `fee-reserve` instead of a generic `LicenseFee`.

`accrue()` consumes this unbounded value directly against live pool state: [2](#0-1) 

Specifically:
```
(reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
(treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0))
```
`(- (total-assets-preview) reserve-inc)` is an unchecked Clarity `uint` subtraction. If `reserve-inc >= (total-assets-preview)`, this is a runtime arithmetic underflow, which Clarity does not treat as a recoverable `(err ...)` — it aborts the transaction outright and `try!` cannot intercept it.

Because `debt-delta` (accrued interest since `last-update`) grows with elapsed time and `total-assets-preview` reflects only currently deposited liquidity, a `fee-reserve` set close to `BPS` combined with a large enough accrual window (e.g., interest compounding over a long gap between calls, or high-utilization/high-rate periods) makes `reserve-inc` approach or exceed the pool's total assets. Once that threshold is crossed, every subsequent call to `accrue()` — hence every `deposit`, `redeem`, `system-borrow`, and `repay` — panics.

### Impact Explanation
Every state-changing vault entrypoint calls `accrue()` as its first step. A single underflowing `accrue()` call permanently bricks minting, burning, borrowing, and repaying on that vault until the fee is lowered or the state that produces the underflow is otherwise fixed by governance — this is a temporary freezing of funds for all depositors and borrowers of the vault, matching the in-scope "temporary freezing of funds" impact class.

### Likelihood Explanation
`fee-reserve` is settable up to just under 100% with a single `set-fee-reserve` call and no additional cap; no compromise of the DAO's identity or registries is required, only use of the existing parameter-update path exactly as designed. Combined with normal, expected growth of `debt-delta` relative to `total-assets-preview` (e.g., low pool liquidity relative to outstanding debt, or a long gap between `accrue()` invocations), the underflow condition is reachable through ordinary protocol operation, not an edge case requiring privileged compromise.

### Recommendation
Introduce an explicit `max-fee-reserve` well below `BPS` (mirroring the source report's `maxLicenseFee` recommendation), and additionally bound `reserve-inc` so it can never reach `(total-assets-preview)` — e.g. clamp `reserve-inc` to `(- (total-assets-preview) u1)` or revert with a defined error instead of relying on the unchecked subtraction. Apply the same fix to `fee-reserve`/`accrue` in every other vault file (`v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`), since all share the identical pattern.

### Proof of Concept
1. DAO calls `set-fee-reserve` with a value close to `BPS` (e.g. `u9999`), which passes the sole check `(asserts! (< val BPS) ERR-RESERVE-VALIDATION)`. [3](#0-2) 
2. Time elapses (or a large borrow accrues interest) such that `debt-delta` in the next `accrue()` call is large relative to the vault's `total-assets-preview` (e.g., a vault with low deposited liquidity but sizeable outstanding debt).
3. Any user calls `deposit`, `redeem`, `system-borrow`, or `repay`; each internally calls `(try! (accrue))`. [4](#0-3) 
4. Inside `accrue()`, `reserve-inc` (≈99.99% of `debt-delta`) is computed and compared implicitly via `(- (total-assets-preview) reserve-inc)`; once `reserve-inc >= (total-assets-preview)`, this subtraction underflows and the transaction aborts.
5. Because this computation runs unconditionally at the top of every state-changing entrypoint, all subsequent deposits/redeems/borrows/repays on the vault revert, freezing user funds until governance intervenes to lower `fee-reserve` (if even still possible, since `set-fee-reserve` itself also calls `(try! (accrue))` at line 651, which would also panic).

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L647-664)
```text
(define-public (set-fee-reserve (val uint))
  (begin
    (try! (check-dao-auth))
    (asserts! (< val BPS) ERR-RESERVE-VALIDATION)
    (try! (accrue))
    
    (print {
      action: "vault-set-fee-reserve",
      caller: tx-sender,
      data: {
        vault: UNDERLYING,
        old-value: (var-get fee-reserve),
        new-value: val
      }
    })
    
    (var-set fee-reserve val)
    (ok true)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L833-863)
```text
;; -- Lending operations -----------------------------------------------------

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
