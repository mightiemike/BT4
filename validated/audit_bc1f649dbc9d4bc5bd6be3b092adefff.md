### Title
Stale per-timestamp index cache lets a `liquidate-multi` batch reprice zToken collateral after a same-transaction bad-debt socialization - (File: `local-testing/contracts/market/market.clar`, `local-testing/contracts/vault/vault-sbtc.clar`)

### Summary
`market.clar` memoizes vault liquidity/borrow indexes per `(stacks-block-time, aid)` pair via `accrue-and-cache`, assuming an index for a given asset only changes as a function of block time. `liquidate-multi` iterates a batch of independent liquidations (`(ok (map call-liquidate positions))`) inside a single transaction/timestamp. However, `socialize-debt` in the vault contracts directly mutates `lindex` (the liquidity index that prices zTokens) as a side effect of bad-debt write-off performed from *inside* `liquidate`. Because this mutation does not go through `accrue`/`vault-accrue`, it never invalidates the market's `index-cache` entry for that `(timestamp, aid)`. A later position processed in the *same* `liquidate-multi` call that needs the price of a zToken backed by the same vault will read the now-stale cached index instead of the freshly mutated `lindex`.

### Finding Description
1. `accrue-and-cache` in `market.clar` caches indexes keyed only by `{timestamp: stacks-block-time, aid: aid}` and returns the cached value on a hit without re-querying the vault: [1](#0-0) 

2. `liquidate` first accrues/caches indexes for every asset in the borrower's position (`accrue-user-debts`, `accrue-user-collateral`), and only afterwards resolves USD values from that cache: [2](#0-1) 

3. When a liquidation leaves the borrower with no collateral, `liquidate` invokes bad-debt socialization on the debt asset's vault via `vault-socialize-debt`: [3](#0-2) 

4. The vault's `socialize-debt` function directly writes a new `lindex` (and reduces `total-borrowed`/`assets`) as a consequence of writing off bad debt - this bypasses the normal time-based `accrue` path entirely: [4](#0-3) 

5. `liquidate-multi` runs a list of independent `liquidate` calls (each potentially triggering step 3/4) inside one transaction, i.e., one fixed `stacks-block-time`: [5](#0-4) 

Because the `index-cache` map is keyed only by `(timestamp, aid)` and is never cleared/updated when `socialize-debt` changes a vault's `lindex` mid-batch, any position processed later in the same `liquidate-multi` call that shares the same collateral/debt `aid` will price that asset using the pre-socialization index cached earlier in the same transaction - a classic "cached value not invalidated when its source moves" defect, occurring entirely within a single transaction/block and requiring no reentrancy.

### Impact Explanation
zToken prices derived from a stale `lindex` will misstate collateral/debt USD values for subsequent positions in the same batch. Depending on direction of the socialization-induced index change, this can cause:
- Liquidators seizing more collateral than the correct post-socialization valuation entitles them to (direct value extraction from the borrower/protocol), or
- Under-collection of debt for a subsequent position, leaving additional bad debt that must later be socialized, degrading protocol solvency.

This lands on theft of funds / protocol insolvency style impact depending on the direction of the index move, satisfying the Critical/High impact bar for value miscalculation in an in-scope lending flow.

### Likelihood Explanation
Requires: (a) a `liquidate-multi` batch containing at least two positions, (b) the first liquidated position triggering bad-debt socialization on a vault whose zToken is also used as collateral/debt in a later position in the same batch, and (c) both events happening at the same `stacks-block-time` (guaranteed, since it's the same transaction). Given the protocol explicitly designed `liquidate-multi` to process independent positions together specifically "to prevent front-running attacks that prevent bad debt socialization," this cross-position interaction inside one batch is a realistic and intended usage pattern, making the trigger conditions plausible rather than contrived.

### Recommendation
Invalidate or bypass the `index-cache` entry for an asset whenever `socialize-debt` (or any other function that mutates `lindex`/vault state outside the normal `accrue` path) is executed within the same transaction, e.g., by removing the cache entry for that `aid` after `vault-socialize-debt` returns, or by re-deriving vault indexes freshly for every position processed inside `liquidate-multi` rather than relying on the timestamp-keyed cache across positions.

### Proof of Concept
1. Attacker (liquidator) submits `liquidate-multi` with two entries:
   - Position A: borrower whose full collateral will be seized, driving `no-collateral-left` true and triggering `vault-socialize-debt` on vault V (e.g., `vault-sbtc`), which calls `socialize-debt` and updates `lindex` for V.
   - Position B: a separate borrower holding zToken collateral backed by the same vault V (e.g., zsBTC), to be liquidated in the same call.
2. During processing of Position A (first in the `map call-liquidate` fold), `accrue-and-cache` for vault V's `aid` is populated in `index-cache` at the current `stacks-block-time` (cache miss → store).
3. Still while processing Position A, bad-debt socialization runs and directly calls `(var-set lindex new-lindex)` in `vault-sbtc.clar`, changing V's true liquidity index.
4. When Position B is processed next in the same transaction/timestamp, `accrue-and-cache` for the same `aid`/timestamp is now a cache HIT and returns the pre-socialization index instead of re-querying V's now-updated `lindex`.
5. Position B's zToken collateral/debt is valued using the stale index, allowing the liquidator to seize an incorrect (favorable) amount of collateral relative to the vault's true state.

Note: I was unable to directly view the body of the private `call-liquidate` helper (only its usage site in `liquidate-multi` was located), so I cannot 100% confirm whether it wraps `liquidate` with additional safeguards beyond error-catching. The staleness mechanism in `accrue-and-cache` and the direct `lindex` mutation in `socialize-debt` are both confirmed from source, and their interaction inside a `liquidate-multi` batch is the basis of this finding.

### Citations

**File:** local-testing/contracts/market/market.clar (L253-265)
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

**File:** local-testing/contracts/market/market.clar (L1428-1436)
```text
    ;; accrue FIRST - populates cache for zToken price resolution
    (u-debt (accrue-user-debts (get debt pos-full)))
    (u-coll (accrue-user-collateral (get collateral pos-full)))

    ;; NOW safe to resolve prices (cache is populated)
    (assets (get-assets mask))
    (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
    (total-collateral-usd (get collateral notional-valued-assets))
    (total-debt-usd (get debt notional-valued-assets))
```

**File:** local-testing/contracts/market/market.clar (L1557-1571)
```text
      ;; Handle bad debt socialization if no collateral left
      (let ((bad-debt-socialized 
              (if no-collateral-left
                  (let ((stripped-debt-list (filter-out-debt-asset (get debt pos-full) debt-aid))
                        (fresh-debt-list (if (is-eq debt-updated u0)
                                             stripped-debt-list
                                             (unwrap-panic (as-max-len?
                                               (append stripped-debt-list
                                                       { aid: debt-aid, scaled: debt-updated })
                                               u64)))))
                    (if (> (len fresh-debt-list) u0) ;; if still has debt
                      (let ((socialization-result (fold socialize-debt-asset 
                                                        fresh-debt-list 
                                                        { borrower: borrower, success: true })))
                        (asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
```

**File:** local-testing/contracts/market/market.clar (L1610-1622)
```text
;; Liquidates multiple positions atomically
;; Each position can have different: borrower, collateral asset, debt asset, and debt amount
;; Prevents front-running attacks that prevent bad debt socialization
;; Note: price-feeds not supported in batch - update prices separately or use individual liquidate()
;; Returns list of responses - one per position (ok/err)
;; Failed liquidations return error codes but don't revert entire batch
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
```

**File:** local-testing/contracts/vault/vault-sbtc.clar (L961-986)
```text

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

    (print {
      action: "socialize-debt",
      caller: contract-caller,
      data: {
        scaled-amount: scaled-amount,
        debt-reduction: debt-reduction,
        principal-reduction: principal-reduction,
        old-lindex: current-lindex,
        new-lindex: new-lindex,
        old-total-assets: old-total-assets,
        principal-scaled: (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0),
        total-borrowed: (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0),
        index: idx
      }
    })

    (ok true)))
```
