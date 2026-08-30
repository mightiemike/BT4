### Title
`write-feeds` fold on Pyth price updates short-circuits and skips remaining feed updates when one feed fails - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`write-feeds` folds `write-feed` over an optional list of up to 3 Pyth price-feed update buffers. If any single feed's `verify-and-update-price-feeds` call fails, `write-feed` returns `ERR-PRICE-FEED-UPDATE-FAILED`, and every subsequent element in the fold is short-circuited via the `error-status status` branch without attempting its own update. This mirrors the reported ZetaChain bug class: one failing item in a batch causes the remaining, independent items to be skipped instead of being processed on their own merits.

### Finding Description
`write-feed` is defined as a fold accumulator function: [1](#0-0) 

and `write-feeds` folds it over the caller-supplied list of feeds: [2](#0-1) 

The accumulator pattern is: `(fold write-feed entries (ok true))`. On each iteration, `write-feed` `match`es the running accumulator (`status`):
- If `status` is currently `(ok true)` (`success-status` branch), it attempts to update that feed and returns either `(ok true)` or `ERR-PRICE-FEED-UPDATE-FAILED`.
- If `status` is already an error (`error-status` branch), it just returns the existing error untouched, meaning the feed for that iteration is never even attempted.

So once the first feed in the list fails, every feed after it in the same `(list 3 (buff 8192))` is silently skipped — the fold "absorbs" the failure and propagates it forward instead of continuing to process the remaining, independent price feeds. This is invoked from market entry points (borrow/repay/liquidate flows accepting an optional `price-feeds` parameter) where `try!` on the overall result means the entire user transaction reverts, and none of the 2nd/3rd feeds get updated even though they had nothing to do with the first feed's failure.

### Impact Explanation
Because unrelated price feeds in the same batch are skipped after one failure, a transaction relying on multiple oracle updates (e.g., updating collateral asset price and debt asset price together before a liquidation or borrow) can have its legitimate, independent feed updates dropped. This can cause stale prices to persist for assets whose feed update was skipped, which can lead to positions being evaluated against stale/incorrect prices during borrow, repay, or liquidation — a form of temporary freezing/mis-valuation of user funds, since a legitimately updatable feed does not get refreshed due to an unrelated feed's failure in the same call.

### Likelihood Explanation
Likelihood is moderate: it requires one of the up to 3 feeds submitted in a single call to fail verification (e.g., stale Wormhole VAA, decode failure, or price-feed-specific issue) while the others are valid and updatable. Given feeds are bundled by the caller/keeper (not necessarily the protocol itself), any off-by-one issue, expired attestation, or malformed buffer for one asset in the batch will cascade to prevent updates for the rest of the batch.

### Recommendation
Change `write-feed`'s fold semantics so failures for one feed don't block subsequent feeds: process each entry independently (e.g., collect a list of per-feed results, or only skip further processing when explicitly desired), rather than propagating the first error through the rest of the fold via the `error-status` pass-through branch.

### Proof of Concept
1. Caller invokes a market function (e.g., `liquidate` or `borrow` — check individual function's signature) passing `price-feeds` as `(some (list feed-a feed-b feed-c))`, where `feed-a` is for the collateral asset and `feed-b`/`feed-c` are valid updates for other assets used in the same evaluation.
2. `write-feeds` is called: `(fold write-feed (list feed-a feed-b feed-c) (ok true))`.
3. First fold step processes `feed-a` against accumulator `(ok true)`; suppose Pyth verification fails (e.g., stale attestation) → returns `ERR-PRICE-FEED-UPDATE-FAILED`.
4. Second fold step receives accumulator = error; `write-feed`'s `match` hits the `error-status status` branch, returning the same error without even attempting `feed-b`'s update.
5. Third fold step: same — `feed-c` is never attempted either.
6. `write-feeds` returns `ERR-PRICE-FEED-UPDATE-FAILED`; the calling function's `try!` on this result reverts the whole transaction — `feed-b` and `feed-c`, which were valid and could have updated their respective oracle prices on-chain, are never applied, leaving those price feeds stale for subsequent calls in later blocks that rely on `oracle-timestamp-fresh`/`last-update` freshness checks. [3](#0-2)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L128-152)
```text
;; Write a single Pyth price feed update using fold accumulator pattern
(define-private (write-feed (feed (buff 8192)) (status (response bool uint)))
  (match status
    success-status
      (match (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-oracle-v4 verify-and-update-price-feeds
          feed
          {
            pyth-storage-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4,
            pyth-decoder-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-pnau-decoder-v3,
            wormhole-core-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.wormhole-core-v4,
          }
        )
        update-success (ok true)
        update-failed ERR-PRICE-FEED-UPDATE-FAILED)
    error-status status
  )
)

;; Process optional list of price feed updates
;; If list is provided, folds over it and updates all feeds
;; If list is none, does nothing (allows for backward compatibility)
(define-private (write-feeds (feeds (optional (list 3 (buff 8192)))))
  (match feeds
    entries (fold write-feed entries (ok true))
    (ok true)))
```
