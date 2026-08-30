### Title
Stale `index-cache` liquidity index used for zToken pricing after `socialize-debt` write-down within the same block - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` caches each vault's borrow/liquidity index once per block in the `index-cache` map, keyed only by `{ timestamp: stacks-block-time, aid }`. The cache is populated the first time any user action triggers `accrue-and-cache` for that vault in the current block, and is reused for every subsequent price resolution in the same block without re-checking the vault. However, `socialize-debt` in the vault contracts writes the vault's `lindex` directly, bypassing the normal `accrue` path that the market's cache is meant to track. If `socialize-debt` is invoked after the market has already cached that vault's index for the current block, every zToken price resolution for the rest of the block keeps using the pre-write-down (inflated) liquidity index.

### Finding Description
`accrue-and-cache` implements a "cache hit / cache miss" pattern keyed purely by block timestamp: [1](#0-0) 

Once a vault's indexes are cached for the current `stacks-block-time`, `get-cached-indexes` returns that cached tuple for the remainder of the block without ever calling the vault again: [2](#0-1) 

This cached `lindex` is exactly what `resolve-ztoken` uses to convert a base oracle price into the zToken's USD price: [3](#0-2) 

The vault's `socialize-debt` function, however, mutates `lindex` directly and does not go through `accrue()`/the market's caching hook at all — it reads `index`/`lindex` straight from `var-get`, computes a write-down, and stores the new (lower) `lindex`: [4](#0-3) 

`market.clar` routes bad-debt socialization to this vault entrypoint via `vault-socialize-debt`: [5](#0-4) 

Because `index-cache` is a durable contract map (not a transient/transaction-scoped variable), any earlier transaction in the same block that triggered `accrue-and-cache` for a vault (e.g., a normal borrow/repay/deposit touching that vault, or a prior liquidation that priced the corresponding zToken as collateral) leaves a stale, pre-write-down `lindex` cached under `{ timestamp: stacks-block-time, aid }`. A subsequent transaction in the *same block* that socializes bad debt for that vault mutates the vault's real `lindex` downward, but the market's cached copy is never invalidated — `get-cached-indexes`/`resolve-ztoken` keep returning the old, higher `lindex` for every zToken price computation until the block timestamp changes.

### Impact Explanation
While the stale cache persists (rest of the block after socialization), any user action that prices the affected zToken as collateral (borrow, health check, liquidation trigger) uses an inflated collateral value derived from the pre-write-down liquidity index. This lets a position that should be under-collateralized after the loss socialization pass health checks and allows additional borrowing against collateral that is not actually backed by that much value — a temporary but real overstatement of protocol solvency/backing for the duration of the block, directly affecting funds at risk (protocol insolvency / freezing of the true collateral backing) rather than being a mere UI inconsistency.

### Likelihood Explanation
Requires only ordinary, in-scope interactions in a single block: (1) any transaction that triggers `accrue-and-cache` for the ztoken's underlying vault (extremely common — happens on virtually all borrow/repay/deposit/redeem/liquidation calls touching that asset), followed later in the same block by (2) a `socialize-debt` call for that same vault (a normal, expected bad-debt handling operation after an under-collateralized liquidation). No privileged access or governance action is needed; ordering within a block is influenced by transaction fees/mempool, which is achievable by a normal actor.

### Recommendation
Invalidate or refresh the market's `index-cache` entry for a vault whenever that vault's `lindex`/`index` changes outside the cached `accrue-and-cache` path — e.g., have `socialize-debt` (and any other function that mutates `lindex`/`index` directly) call back into `market.clar` to overwrite/delete the corresponding `index-cache` entry, or have `market.clar` compare the vault's live `last-update`/`lindex` against the cached entry before trusting it rather than trusting the cache unconditionally for the whole block.

### Proof of Concept
1. Block N, Tx 1: A user borrows/repays/deposits against `vault-stx`, which calls `market.clar`'s `accrue-and-cache(STX)` → `index-cache[{timestamp: T, aid: STX}]` is populated with `{index: I0, lindex: L0}` (see `accrue-and-cache`, `mainnet/contracts/market/v0-4-market.clar:245-257`).
2. Block N, Tx 2: A liquidation triggers `vault-socialize-debt(STX, scaled-amount)` → `.v0-vault-stx socialize-debt` computes and stores a lower `lindex = L1 < L0` directly via `var-set lindex new-lindex` (`mainnet/contracts/vault/v0-vault-stx.clar:944-984`), without touching `index-cache`.
3. Block N, Tx 3 (same block): A different user borrows using `zSTX` as collateral. `price-resolve` → `resolve-callcode` → `resolve-ztoken` calls `get-cached-indexes(STX)`, which still returns `L0` from `index-cache[{timestamp: T, aid: STX}]` (`mainnet/contracts/market/v0-4-market.clar:365-369, 944-946`), overvaluing the `zSTX` collateral relative to its true post-socialization backing (`L1`).
4. The over-valued collateral lets Tx 3's health check pass with more borrowing power than the true, updated `lindex` would allow, persisting until `stacks-block-time` advances to the next block.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L944-946)
```text
(define-read-only (get-cached-indexes (aid uint))
  (map-get? index-cache { timestamp: stacks-block-time, aid: aid }))

```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-984)
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
