Found a directly relevant analog: the `accrue()` "pause pass-through" pattern in the vault contracts explicitly matches one of the listed root-cause mechanisms ("a pause that passes through instead of reverting").

### Title
Accrual pause silently freezes the interest/liquidity index while dependent health and debt calculations keep advancing time - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
The vault's `accrue` function, when the `accrue` pause flag is set, does not revert - it silently returns the *current* (stale) `index`/`lindex` values as if accrual succeeded: `(if (get accrue states) (ok { index: idx, lindex: lidx }) ...)` [1](#0-0)  . Every other public entry point (`deposit`, `redeem`, `system-borrow`, `system-repay`, and the market's `accrue-and-cache`) calls `accrue()` first and treats its `ok` result as authoritative, using it to compute scaled-debt/collateral conversions and health checks, without distinguishing "index genuinely refreshed" from "accrual paused, value frozen and stale."

### Finding Description
`accrue()` is the single source of truth for the vault's borrow index (`index`) and liquidity index (`lindex`), which every debt/collateral USD notional calculation in `market.clar` depends on via `accrue-and-cache` [2](#0-1) . When `pause-states.accrue` is true, `accrue()` intentionally skips computing `next-index`/`next-liquidity-index` and just re-returns the currently stored `idx`/`lidx` wrapped in `ok`, without updating `last-update` [3](#0-2) .

Because the return is `(ok ...)` rather than an error, callers such as `system-borrow`/`system-repay` proceed as if the vault state is current - they read `(var-get index)` right after calling `accrue` and use it to convert `amount` to `scaled-amount` for debt bookkeeping [4](#0-3) . Likewise, market.clar's health/notional calculations (`get-notional-evaluation`, `calculate-asset-notional-value`) rely on `accrue-and-cache` returning the "real" index to price outstanding scaled debt into USD terms for the LTV check in `borrow()` [5](#0-4) .

While the pause is active, real-world time (and thus real economic interest accrual that *should* happen) continues to elapse, but the vault's index is frozen and reported as fresh/valid rather than being rejected. This is the same class of defect as "a pause that passes through instead of reverting": a downstream consumer (health check, debt conversion) is fed a value whose staleness is masked by a success response, letting borrow/health calculations proceed using economically stale interest data instead of failing safe. Since `last-update` is also only advanced when the index actually changes (a second instance of "clock advanced only on change" in the same function) [6](#0-5) , once the pause is lifted the very next `accrue()` call computes `next-index()` over the *entire* paused duration in one jump (since `last-update` never moved during the pause), applying a lump-sum interest jump rather than having failed the operations that occurred during the pause.

### Impact Explanation
During the accrual pause window, `borrow()`'s LTV health check [7](#0-6)  and `system-borrow`'s debt-cap check [8](#0-7)  are evaluated against a frozen index instead of reverting, meaning debt/interest accounting silently diverges from reality while still being treated as valid for new borrows and repayments. This can let borrowers avoid interest that should be accruing (temporary freezing/misallocation of yield owed to suppliers) and, since the jump is applied all at once on unpause, can also produce an abrupt state transition affecting outstanding positions' health computations. This lands in the **temporary freezing of funds / unclaimed yield** impact category, since supplier yield accrual is paused/misrepresented as current rather than the operation being reverted.

### Likelihood Explanation
This requires only that the DAO/admin-controlled `accrue` pause be toggled on for a vault (a normal, expected operational state per the pausability design docs) and that a user interact with `deposit`, `redeem`, `borrow`, or `repay` during that window - no attacker collusion or privileged compromise is needed beyond the intended pause mechanism itself being exercised. It is a single-transaction / single-block observable effect (any tx that calls `accrue()` while paused gets the stale-as-if-fresh index), making it straightforward to trigger.

### Recommendation
`accrue()` should not return `ok` with an unrefreshed index while claiming success; either return a distinguishing flag (e.g., `{index, lindex, refreshed: bool}`) that callers explicitly check, or have accrual-dependent operations (`borrow`, `system-borrow`, `system-repay`, health checks) revert while the `accrue` pause is active, consistent with how other pause flags (`deposit`, `redeem`, `collateral-remove`, `debt-add`) correctly `asserts!`-revert instead of passing through.

### Proof of Concept
1. DAO/admin sets `pause-states.accrue = true` on `v0-vault-usdc` (or any vault).
2. Time passes (real interest should accrue on outstanding debt/supply).
3. A user calls `market.borrow(usdc, amount, ...)`, which calls `accrue-and-cache` → vault `accrue()`; because `accrue` is paused, `accrue()` returns `(ok {index: idx, lindex: lidx})` with the pre-pause stale values [9](#0-8) .
4. `market.borrow`'s health check and debt conversion (`convert-to-scaled-debt`, `get-notional-evaluation`) use this stale index as if it were current, allowing the borrow to proceed under outdated interest accounting [10](#0-9) .
5. When the pause is lifted, the next `accrue()` call computes `next-index()`/`next-liquidity-index()` over the full elapsed paused duration in a single jump (since `last-update` was frozen), applying a step change to all positions' actual debt/value that were computed as "healthy" against the stale index during the pause.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L833-861)
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L863-898)
```text
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
    (asserts! (not (get borrow states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (<= amount available-assets) ERR-INSUFFICIENT-VAULT-LIQUIDITY)
    (asserts! (<= (+ debt amount) CAP-DEBT) ERR-DEBT-CAP-EXCEEDED)

    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed (+ (var-get total-borrowed) amount))
    (try! (send-underlying amount receiver))

    (print {
      action: "system-borrow",
      caller: contract-caller,
      data: {
        receiver: receiver,
        amount: amount,
        scaled-amount: scaled-amount,
        principal-scaled: updated-scaled-principal,
        total-borrowed: (var-get total-borrowed),
        index: idx
      }
    })

    (ok true)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L245-257)
```text
(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))

    (match cached?
      ;; cache HIT: return cached value (1 read only)
      cached-indexes (ok cached-indexes)

      ;; cache MISS: accrue and cache (vault-accrue now returns indexes)
      (let ((indexes (try! (vault-accrue aid))))
        ;; store in cache
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1246-1296)
```text
        ;; Step 1: Get position WITHOUT resolving prices
        (position (try! (get-position account)))
        (mask (get mask position))
        
        ;; Step 2: Accrue user's positions (populates cache for ztokens)
        (u-debt (accrue-user-debts (get debt position)))
        (u-coll (accrue-user-collateral (get collateral position)))
        
        ;; Step 3: Accrue the asset being borrowed (needed for index access)
        (unused (accrue-and-cache asset-id))
        
        ;; Step 4: NOW safe to resolve prices (cache is populated)
        (assets (get-assets mask))

        ;; Calculate current health with current mask
        (current-group (try! (get-egroup mask)))
        (current-ltvb (buff-to-uint-be (get LTV-BORROW current-group)))

        ;; LTV
        (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
        (collateral-value (get collateral notional-valued-assets))
        (debt-value (get debt notional-valued-assets)))

    ;; preconditions
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (get debt asset) ERR-BORROW-DISABLED)
    (asserts! (is-healthy collateral-value debt-value current-ltvb) ERR-UNHEALTHY)

    ;; Calculate FUTURE debt (after adding this debt)
    ;; For debt: bit position = asset-id + 64 (DEBT-OFFSET)
    (let ((future-mask (bit-or mask (pow u2 (+ asset-id DEBT-OFFSET))))
          (future-group (try! (get-egroup future-mask)))
          ;; Per-egroup borrow disable check (uses FUTURE egroup, not current)
          ;; Each bit in BORROW-DISABLED-MASK corresponds to a debt asset ID (NOT offset by 64)
          (disabled-borrow-mask (get BORROW-DISABLED-MASK future-group))
          (debt-increase (try! (get-asset-value asset amount true)))
          (debt-post-increased (+ debt-value debt-increase)))

    ;; Check if this specific asset is disabled for borrowing in the FUTURE egroup
    (asserts! (is-eq (bit-and disabled-borrow-mask (pow u2 asset-id)) u0) ERR-EGROUP-ASSET-BORROW-DISABLED)
    ;; postconditions
    (asserts! (try! (is-healthy-with-mask collateral-value debt-post-increased future-mask)) ERR-UNHEALTHY)

    (try! (vault-system-borrow asset-id amount funds-receiver))
    (let ((scaled-debt-added (convert-to-scaled-debt asset-id amount true))
          (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id)))))
      (try! (contract-call? .v0-market-vault
                            debt-add-scaled
                            account
                            scaled-debt-added
                            asset-id))
```
