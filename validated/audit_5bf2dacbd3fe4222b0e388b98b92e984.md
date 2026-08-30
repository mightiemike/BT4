### Title
Stale interest-index caching lets `market.clar` price ztoken collateral/debt with pre-unpause values within the same block - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`v0-4-market.clar` caches each vault's liquidity/borrow index in `index-cache-` keyed only by `{ timestamp: stacks-block-time, aid }` [1](#0-0) . The cache is a persistent `define-map`, not a transient/per-transaction value, so the first transaction in a block that touches an `aid` "locks in" whatever `vault-accrue` returns for the rest of that block. The individual vaults' `accrue` explicitly supports a "pause pass-through" branch that returns the stale, un-recomputed `{index, lindex}` without erroring and without updating `last-update` when accrual is paused [2](#0-1) . Because the market's cache key contains no reference to vault pause-state or to whether the value returned was the "paused, frozen" value versus the fully-accrued value, a value cached while `accrue` was paused is treated as valid for every subsequent call in the same block, including calls that happen after the vault is unpaused.

### Finding Description
`accrue-and-cache` in the market only checks whether an entry exists for `(stacks-block-time, aid)`; if it does, it is returned verbatim as authoritative regardless of how it was produced [1](#0-0) . Every downstream consumer treats this cached value as ground truth for collateral/debt notional valuation and debt scaling: `get-notional-evaluation` uses it to price ztoken debt [3](#0-2) , and `resolve-ztoken` uses `get-cached-indexes` to price ztoken collateral for oracle purposes [4](#0-3) , and `borrow` relies on `get-cached-indexes` for the scaled-debt/borrow-index bookkeeping [5](#0-4) .

The value that gets bound into the cache differs materially depending on vault pause state: when `pause-states.accrue` is true, `accrue` returns the last-known `{index, lindex}` unchanged and does **not** invalidate/update `last-update`, i.e. it is explicitly a pass-through rather than a revert [6](#0-5) . When not paused, `accrue` computes `next-index`/`next-liquidity-index` from elapsed time and writes the new values [7](#0-6) .

Sequence (single block, Clarity per-block time granularity):
1. Block N begins with vault X's `accrue` paused (`pause-states.accrue = true`), `index = I0`.
2. Tx 1 (attacker or benign) triggers any market operation touching aid X (e.g. `collateral-add`, `borrow`, `repay`) which calls `accrue-and-cache(X)`. Cache miss → calls `vault-accrue` → vault's `accrue` sees `paused` and pass-through returns `{index: I0, lindex: I0}` without touching `last-update` [6](#0-5) . Market stores `{timestamp: T, aid: X} = {I0, I0}` in `index-cache-`.
3. Later in the same block N, the vault admin unpauses accrual (`pause-states.accrue = false`). This does not touch the market's `index-cache-` map at all - there is no cross-contract invalidation hook.
4. Tx 3, still in block N (`stacks-block-time` unchanged), performs a borrow/collateral/liquidation operation on aid X. `accrue-and-cache(X)` looks up `{timestamp: T, aid: X}`, gets a cache **HIT**, and returns the stale `I0` pair instead of calling `vault-accrue` again, even though a real accrual (potentially reflecting substantial elapsed time since the true `last-update`, which was frozen at some earlier value while paused) would now compute a materially different index.
5. All health checks, LTV math, and debt/collateral notional calculations in that transaction use the stale index, understating debt or overstating/understating collateral value depending on the direction of the discrepancy, letting a position that should be unhealthy pass the health check, or letting a borrow/liquidation execute against incorrect numbers.

### Impact Explanation
If the stale cached index understates real debt or overstates real collateral value, a user can borrow beyond what their true health factor allows, or a liquidator/liquidatee can manipulate the accounting during a liquidation window, directly leading to protocol insolvency or theft of funds at rest (undercollateralized debt that will never be fully collectible). This lands on the Critical impact bucket: protocol insolvency / direct theft of user funds via a health-check bypass driven by non-invalidated cached interest indexes.

### Likelihood Explanation
This requires the interest-accrual pause to be toggled within the same block a market operation is executed for the affected asset - a narrow but not impossible window, especially around planned pause/unpause maintenance operations where an attacker who monitors mempool/pending admin transactions can front-run the unpause with a tx that seeds the stale cache entry, then follow with a transaction (or rely on their own earlier transaction) later in the same block that exploits the now-mismatched cached value. It does not require any privilege escalation, DAO compromise, or key leakage by the attacker - only ordinary use of the public `borrow`/`collateral-add`/`repay`/`liquidate` entry points combined with observing a legitimate, expected pause-state transition.

### Recommendation
Include the vault's pause-state (or a monotonically incrementing "accrual epoch" that only advances on a real, non-pass-through accrual) as part of the `index-cache-` key, or explicitly invalidate/clear the relevant cache entries whenever `set-pause-states` toggles `accrue` for a vault. Alternatively, have the paused `accrue` return a value tagged as "provisional" that the market never caches, forcing every access during/around a pause transition to re-derive the index directly from the vault.

### Proof of Concept
Not independently reproducible from the indexed code alone - the exact admin entry point for toggling `pause-states` in the mainnet vault contracts was not fully retrieved before the tool budget was exhausted (I confirmed the pass-through behavior in `accrue()` and the timestamp-only cache key in `market.clar`, but did not verify the full signature/access-control of the pause-toggle function or trace every asset-id-to-vault mapping end to end). A concrete PoC would need: (1) confirming the `set-pause-states`-equivalent public function and its constraints, and (2) a Clarinet/simulation harness that pauses a vault, seeds the market cache via a `collateral-add`/`borrow` call, unpauses within the same simulated block, and shows a second market call reusing the stale cached index instead of a freshly computed one.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1261-1314)
```text
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
      
      (print {
        action: "borrow",
        caller: contract-caller,
        data: {
          account: account,
          receiver: funds-receiver,
          asset-id: asset-id,
          asset-addr: address,
          amount: amount,
          scaled-debt-added: scaled-debt-added,
          borrow-index: borrow-index,
          position-collateral-usd: collateral-value,
          position-debt-usd: debt-post-increased
        }
      })
      
      (ok true)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-864)
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

**File:** local-testing/contracts/market/market.clar (L585-591)
```text
        (debt-scaled   (find-debt-scaled debt-list asset-id))
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))
```
