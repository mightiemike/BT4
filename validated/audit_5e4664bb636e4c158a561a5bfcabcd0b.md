### Title
Stale cached vault index used for zToken pricing after `socialize-debt` mutates vault state directly - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`v0-4-market.clar` caches each vault's accrual result (`index`/`lindex`) keyed only by `{timestamp: stacks-block-time, aid}` so that repeated reads within the same block skip re-calling the vault's `accrue`. However, `socialize-debt` writes a new, lower `lindex` directly into vault state without going through `accrue` and without invalidating or updating the market's `index-cache`. Any subsequent lookup for the same asset in the same block-timestamp gets a cache HIT and returns the pre-loss (higher) `lindex`, causing zToken collateral to be overvalued for every action that reads it after the loss has been socialized on-chain.

### Finding Description
The market maintains a per-asset accrual cache: [1](#0-0) 

Reads go through `accrue-and-cache`, which returns the cached `{index, lindex}` without calling the vault at all on a cache HIT: [2](#0-1) 

`vault-socialize-debt` is a thin pass-through to the vault's `socialize-debt`, and never touches `index-cache`: [3](#0-2) 

Inside the vault, `socialize-debt` reads the current `lindex` and `assets` directly from vault state variables (not via `accrue`, and not via the market's cache) and writes a strictly lower `lindex` when total assets have taken a loss: [4](#0-3) 

This directly mirrors the reported bug class ("a cached value not invalidated when its source moves"): the vault's `lindex` (the source of truth for zToken share price) moves when `socialize-debt` runs, but the market's `index-cache` for that `{timestamp, aid}` pair is left holding the old, higher `lindex`.

Sequence:
1. Within a given `stacks-block-time` T, some operation (e.g., a collateral valuation, borrow, or health check for asset X) calls `accrue-and-cache(X)`. This is a cache MISS, so it calls the vault's `accrue`, and the market stores `{timestamp: T, aid: X} -> {index, lindex}` in `index-cache`.
2. Later in the same block/timestamp, a liquidation or bad-debt event triggers `vault-socialize-debt(X, scaled-amount)`, which calls the vault's `socialize-debt`, directly overwriting the vault's `lindex` data-var to a lower, loss-adjusted value — bypassing `accrue-and-cache` entirely.
3. Any further call to `accrue-and-cache(X)` in the same block-timestamp (e.g., pricing another user's zToken-X collateral, processing another user's borrow/withdraw) gets a cache HIT and returns the stale, pre-loss `lindex` instead of the vault's now-corrected value.
4. Because zToken collateral value/health checks are derived from `lindex`, the reader's collateral is overvalued by exactly the socialized-loss amount, letting them borrow more or withdraw/redeem more than their true post-loss share, at the expense of the remaining zToken holders who should have absorbed that loss.

This is possible under Clarity evaluation order because `stacks-block-time` (and therefore the cache key) is constant across all transactions within the same block, while `var-set lindex` in the vault takes effect immediately for any direct vault read but is invisible to the market's map-based cache until a fresh MISS is forced.

### Impact Explanation
This lets a user extract value that should be socialized as a loss across all zToken holders — either by over-borrowing against artificially overvalued zToken collateral or by redeeming/using collateral at a price that has not yet reflected an on-chain loss. This is a theft of funds at rest (from other zToken holders) and contributes to protocol insolvency, i.e., a Critical-impact class.

### Likelihood Explanation
Requires a `socialize-debt` event (a bad-debt/loss socialization, presumably triggered during liquidation processing) to occur in the same block as another user's collateral-valuation or borrow/redeem transaction for the same asset — a realistic scenario since liquidations and normal user activity can land in the same block, and the cache design makes this a systemic (not one-off) exposure whenever `socialize-debt` fires.

### Recommendation
Invalidate (or update) the `index-cache` entry for the affected `aid` whenever `socialize-debt` (or any other function that mutates vault `lindex`/`index` outside of `accrue`) runs, or remove the cache's reliance on `stacks-block-time` alone and instead always re-read the vault's live state when a socialization event may have occurred earlier in the same block/transaction.

### Proof of Concept
Conceptual PoC (Clarity-level, not exploit code):
1. Tx/Call 1 (or earlier action in the block): market calls `accrue-and-cache(STX)` → cache MISS → vault `accrue` runs → `index-cache[{T, STX}] = {index: I, lindex: L}` is stored.
2. Liquidation flow calls `vault-socialize-debt(STX, scaled-amount)` → vault's `socialize-debt` sets `lindex` to `L' < L` due to a realized loss, but `index-cache[{T, STX}]` is untouched.
3. Victim/attacker action in the same block calls a market function that values zSTX collateral or computes debt, which internally calls `accrue-and-cache(STX)` → cache HIT → returns stale `{index: I, lindex: L}` instead of the corrected `L'`.
4. The user's health check / borrow / redeem uses the inflated `L`, allowing them to borrow more or redeem more underlying than their true post-loss zSTX share entitles them to. [2](#0-1) [5](#0-4)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L112-115)
```text
;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })
```

**File:** mainnet/contracts/market/v0-4-market.clar (L216-223)
```text
(define-private (vault-socialize-debt (aid uint) (amount uint))
  (if (is-eq aid STX) (contract-call? .v0-vault-stx socialize-debt amount)
  (if (is-eq aid sBTC) (contract-call? .v0-vault-sbtc socialize-debt amount)
  (if (is-eq aid stSTX) (contract-call? .v0-vault-ststx socialize-debt amount)
  (if (is-eq aid USDC) (contract-call? .v0-vault-usdc socialize-debt amount)
  (if (is-eq aid USDH) (contract-call? .v0-vault-usdh socialize-debt amount)
  (if (is-eq aid stSTXbtc) (contract-call? .v0-vault-ststxbtc socialize-debt amount)
  ERR-UNKNOWN-VAULT)))))))
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
