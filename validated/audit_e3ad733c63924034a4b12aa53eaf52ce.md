### Title
Accrual pause leaves `last-update` frozen while `redeem`/`deposit` still compute live, un-throttled interest through the preview path - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
Each vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) gates its own `accrue` operation behind a `pause-states` flag that is a *pass-through*, not a revert: when `accrue` is paused, `accrue()` simply returns the stale `index`/`lindex` without touching storage [1](#0-0) . Because the `var-set last-update stacks-block-time` write lives inside the very branch that is skipped while paused, `last-update` never advances during the pause window [2](#0-1) . However, `deposit` and `redeem` are gated by *separate* pause flags (`deposit`/`redeem` in `pause-states`, distinct from `accrue`) [3](#0-2) [4](#0-3) , and they price shares via `convert-to-shares-preview` / `convert-to-assets-preview`, which call `total-assets-preview` → `debt-preview` → a freshly computed `next-index()` regardless of whether `accrue` is paused [5](#0-4) .

### Finding Description
The pause on `accrue` is intended to freeze the vault's interest-index growth (e.g., during an emergency such as a detected rate-curve or index bug). But the freeze only stops the *write* of `index`/`lindex`/`last-update`; it does not stop the *read path* used for pricing deposits/redemptions. Since `last-update` is bound inside the same conditionally-skipped branch, it is left pointing at whatever block time accrual last actually ran — a clock that is "advanced only on change." Meanwhile `next-index()` (invoked transitively by `debt-preview`/`total-assets-preview` inside `deposit`/`redeem`) computes interest as a function of elapsed time since `last-update`, with no dependency on the `accrue` pause flag.

Consequently, while `accrue` is paused but `deposit`/`redeem` are not, a single caller can still invoke `redeem` (or `deposit`) and have their shares priced using a `next-index()` value that reflects the *entire* time elapsed since the last real accrual — including time during which accrual was supposedly frozen for safety. The persisted accounting (`total-assets()`, `total-debt()`, `index`) stays frozen, but the amount actually paid out/priced through the preview functions is not, creating an accounting divergence: `assets` (the actual token-balance ledger, decremented by `inkind` computed from the live preview) can be driven below what `total-debt()`/`index` records support, directly at odds with the reason accrual was paused in the first place.

### Impact Explanation
A single user's `redeem` call converts shares using the un-paused, time-decayed `next-index()` figure while the vault's persisted `index`/`last-update` remain stuck, letting more value flow out (`send-underlying inkind ...`) than the frozen accounting is designed to permit at that block [6](#0-5) . Because `current-assets` is decremented by this inflated `inkind` value, other depositors' claims can become under-collateralized relative to the vault's real backing, resulting in temporary/permanent freezing of remaining depositors' funds (they cannot redeem at par once liquidity/accounting is drained) — a High-severity impact under the "temporary freezing of funds" / insolvency category.

### Likelihood Explanation
This requires no collusion or second attacker: it is exercised entirely by one caller in one transaction (call `redeem` or `deposit` while `accrue` is paused but `redeem`/`deposit` are not). The precondition — accrual paused while deposit/redeem remain open — is a plausible operational state (a DAO might pause only `accrue` to halt interest math during an investigation, while leaving withdrawals open for user safety), making this readily triggerable once that pause configuration exists.

### Recommendation
Either (a) make `redeem`/`deposit`/`system-borrow` also revert (not merely skip) when `accrue` is paused for that asset, or (b) make `total-assets-preview`/`debt-preview`/`next-index()` respect the `accrue` pause flag by short-circuiting to the last persisted `index`/`lindex` (mirroring exactly what `accrue()` itself does when paused), and ensure `last-update` is advanced independently of whether the index actually changed so that resuming from pause does not silently apply the full elapsed-pause duration's interest in one step.

### Proof of Concept
1. DAO calls the pause-setter to set `accrue = true` in `pause-states` for `v0-vault-stx` (leaving `deposit`/`redeem` flags `false`).
2. Time passes (multiple blocks) while `accrue` stays paused: `accrue()` keeps returning the old `{index, lindex}` and never updates `last-update` [7](#0-6) .
3. A user calls `redeem(amount, min-out, recipient)`. Inside, `(try! (accrue))` is a no-op pass-through [8](#0-7) , but `inkind (convert-to-assets-preview amount)` is computed via `total-assets-preview` → `debt-preview` → `next-index()`, which uses the stale `last-update` to compute interest for the *entire* elapsed pause window [9](#0-8) , [10](#0-9) .
4. `send-underlying inkind recipient` pays out this inflated amount and `current-assets` is decremented accordingly [11](#0-10) , while the vault's persisted `index`/`last-update` (used elsewhere for `total-debt`/health checks) remain frozen at the pre-pause values — producing an accounting mismatch that strands value for subsequent redeemers.

**Uncertainty note:** the exact body of `next-index()` was not directly inspected before tool budget ran out; its dependence on `last-update` and elapsed time is inferred from the surrounding accrue logic and the documented interest-curve model (`docs/vaults.md`). This should be confirmed by reading `next-index()`/`next-liquidity-index()` directly in `v0-vault-stx.clar` before treating this as fully proven.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L308-339)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))

(define-private (convert-to-assets-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ta u0)
        u0
        (if (is-eq ts u0)
            u0
            (mul-div-down amount ta ts)))))

;; -- Debt helpers -----------------------------------------------------------

(define-private (total-debt)
  (calc-cumulative-debt (var-get principal-scaled) (var-get index)))

(define-private (debt-preview)
  (calc-cumulative-debt (var-get principal-scaled) (next-index)))

(define-private (total-assets)
  (let ((current-assets (var-get assets))
        (debt (total-debt))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L772-772)
```text
    (asserts! (not (get deposit states)) ERR-PAUSED)
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L797-817)
```text
(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let (
    (states (var-get pause-states))
    (u (try! (accrue)))
    (account contract-caller)
    (current-assets (var-get assets))
    (balance (get-balance-internal account))
    (balance-check (asserts! (>= balance amount) ERR-INSUFFICIENT-BALANCE))
    (available-assets (get-available-assets))
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  (var-set assets (- current-assets inkind))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-863)
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
