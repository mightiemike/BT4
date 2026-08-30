Based on my investigation, I found a concrete analog that matches the approved "pause that passes through instead of reverting" bug class, present identically across all vault contracts in the protocol.

### Title
`accrue` silently returns stale interest index instead of reverting when paused, while share-price previews keep accruing live - (File: `local-testing/contracts/vault/vault-usdc.clar` and all sibling vaults)

### Summary
Every vault contract's `accrue` function is designed so that when the `accrue` pause flag is set, it returns the *unchanged, stale* `index`/`lindex` values with `(ok {...})` instead of reverting, while all operations that call `accrue` as a precondition (`deposit`, `redeem`, `system-borrow`, `flashloan`) continue to execute normally. Meanwhile, the vault's own share-pricing preview functions (`total-assets-preview` → `debt-preview` → `next-index`) compute interest *dynamically from elapsed time*, completely independent of the pause flag and independent of whether `index`/`last-update` were actually committed.

### Finding Description
`accrue` in `vault-usdc.clar` (identical logic replicated in `vault-stx.clar`, `vault-sbtc.clar`, `vault-usdh.clar`, `vault-ststx.clar`, `vault-ststxbtc.clar`, and mainnet `v0-vault-*.clar`) is structured as: [1](#0-0) 

When `(get accrue states)` is true, the function does not revert - it returns `(ok { index: idx, lindex: lidx })` using the values read at the top of the function, i.e. the pre-pause, un-advanced index. `last-update` is likewise left untouched.

However, the conversion functions used to price `deposit`/`redeem` (and by extension all downstream USD valuation of debt in `market.clar`) do not consult this pause flag at all: [2](#0-1) 

`total-assets-preview`/`debt-preview` always call `next-index`, which recomputes interest from `stacks-block-time - last-update` regardless of whether the committed `accrue()` succeeded in advancing state. This is the classic "cached value not invalidated when its source moves" pattern combined with "a pause that passes through instead of reverting": the committed `index` (the cached source of truth used by `market.clar` for scaled-debt→actual-debt conversion during `borrow`/`repay`) is frozen by the pause, but the value used to price real token movements in `redeem`/`deposit` (`convert-to-assets-preview`/`convert-to-shares-preview`) is not frozen and keeps growing with time.

`redeem` uses this live preview to determine `inkind` (the real underlying tokens paid out) and burns shares/transfers real balance based on it: [3](#0-2) 

So while `accrue` is paused: redeemers are paid out at an ever-increasing share price (as if interest is still accruing), but the underlying committed debt-index that borrowers are charged against (read by `market.clar` via cached indexes) never advances, meaning borrowers are never actually charged the "phantom" interest that redeemers are being paid.

### Impact Explanation
This creates a systemic mismatch between assets owed to lenders (growing, per live preview) and assets actually collectible from borrowers (frozen at the paused index). If the pause is engaged for any nontrivial period while deposit/redeem remain active, redemptions drain real underlying liquidity without a corresponding increase in recorded debt, which is a protocol insolvency vector - the vault can be left with insufficient real assets to honor later redemptions at the (correct) implied share price, and the "assets" accounting var itself is never reconciled with the frozen borrower debt.

### Likelihood Explanation
Requires that governance (or whichever admin can call `set-pause-states`) pauses only the `accrue` sub-flag while leaving `deposit`/`redeem`/`borrow` operational - a plausible, single admin action rather than a compromise scenario, since these are independent boolean fields in the same `pause-states` tuple. I was not able to fully confirm within available tool calls whether the individual pause bits are toggled together or independently in practice (i.e., whether operationally `accrue`-only pausing is a supported/likely admin action), which is the main uncertainty in this finding.

### Recommendation
`accrue` should either (a) revert when paused instead of silently returning stale values, consistent with the "pause reverts" pattern used elsewhere, or (b) have `total-assets-preview`/`debt-preview` respect the same pause flag so that share pricing freezes in lockstep with the committed index whenever accrual is paused.

### Proof of Concept
1. Governance calls `set-pause-states` on `vault-usdc.clar` setting only the `accrue` field to `true`, leaving `deposit`/`redeem` unpaused. [4](#0-3) 
2. Time passes across many blocks; borrower debt (tracked via `market.clar`'s scaled debt against the vault's stored `index`) does not grow because every `accrue()` call short-circuits to the stale `index`.
3. A depositor calls `redeem`; `convert-to-assets-preview` computes `inkind` using `debt-preview`/`next-index`, which is computed fresh from elapsed time regardless of the pause, yielding a higher payout than the committed, frozen `index` would justify. [3](#0-2) 
4. Real underlying tokens are sent out via `send-underlying inkind recipient` based on this inflated preview, while the corresponding debt obligation from borrowers was never incremented, leaving the vault under-collateralized relative to its outstanding zToken supply once the pause is lifted and true accounting resumes.

**Note on investigation limits**: I could not conclusively verify, within my remaining tool budget, whether `market.clar`'s debt-scaling functions (`accrue-and-cache`, `get-cached-indexes`) read the vault's committed `index` var directly versus an independently-computed live value, nor whether the `accrue` pause bit is realistically toggled independently of `deposit`/`redeem` in operational practice. This affects confidence in exploitability versus this being an accepted emergency-freeze design tradeoff.

### Citations

**File:** local-testing/contracts/vault/vault-usdc.clar (L797-819)
```text
    (ok inkind)))

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

**File:** local-testing/contracts/vault/vault-usdc.clar (L837-865)
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

**File:** local-testing/contracts/vault/vault-ststxbtc.clar (L332-350)
```text
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

(define-private (total-assets-preview)
  (let ((current-assets (var-get assets))
        (debt (debt-preview))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))
```
