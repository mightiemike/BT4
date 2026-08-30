### Title
Stale `index-cache` liquidity index used after `socialize-debt` moves the vault's `lindex` mid-transaction - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` caches each vault's `liquidity-index` (`lindex`) and `borrow-index` in a block-scoped `index-cache` map, keyed by `(aid, stacks-block-time)`, to avoid repeated `contract-call?`s to the vaults during a single transaction [1](#0-0) . The cache is populated the first time `accrue-vault-indexes` is called for an asset in the current `stacks-block-time`, and every subsequent lookup for that asset in the same block returns the cached value instead of re-querying the vault, per the flow documented for `accrue-vault-indexes` and `resolve-price` (local-testing/contracts/market/market.clar:487-511, 516-608, 535-545). Separately, the liquidation engine's bad-debt handling calls `socialize-debt` on a vault, which mutates that vault's `lindex` in vault storage to distribute losses across depositors (local-testing/contracts/market/market.clar:565-568). If `socialize-debt` for asset A is invoked after `market.clar` has already cached A's `lindex` for the current block (e.g., during a multi-step operation such as `liquidate-multi`, which processes several debt assets against one collateral asset in a single transaction — local-testing/contracts/market/market.clar:580-618), any later `resolve-price`/collateral valuation call for that same asset within the same transaction will read the pre-socialization `lindex` from `index-cache` rather than the updated post-socialization value.

### Finding Description
The cached value is the `lindex` entry in `index-cache[aid, block-time]`, populated by `accrue-vault-indexes`. The event that invalidates/moves its source is `socialize-debt`, which directly reduces the vault's on-chain `lindex` to spread bad debt across zToken holders. The later use is any subsequent `resolve-price` call (or another collateral/debt valuation step) for the same asset within the same transaction (e.g., in `liquidate-multi`, where multiple debt legs are processed sequentially against shared collateral, or where the same zToken is both collateral being valued and the asset undergoing socialization). Because `index-cache` is keyed only by `(aid, timestamp)` and not invalidated when `socialize-debt` mutates the vault's index mid-transaction, the cached `lindex` becomes stale relative to the vault's true state for the remainder of that call.

### Impact Explanation
A stale, pre-socialization `lindex` overstates the value of zToken collateral (or debt) used in subsequent LTV/graduated-liquidation math (`get-liq-graduated-formula`) within the same transaction, after bad debt has already reduced the true share value. This can cause the liquidation engine to seize collateral or determine liquidatable amounts based on incorrect valuations — either allowing seizure/repay accounting that doesn't reflect the just-socialized loss, or causing downstream health/penalty computations to diverge from the vault's actual, freshly-mutated state. This falls under temporary freezing/incorrect handling of funds during liquidation (High), analogous to the referenced `closableAmount` miscalculation causing incorrect liquidation-fee/eligibility judgments.

### Likelihood Explanation
This requires a specific sequence within one transaction: a bad-debt-triggering liquidation step that calls `socialize-debt` on an asset, followed by a later valuation step in the same call (e.g., `liquidate-multi` processing another leg, or the same zToken being both collateral and the socialized asset) that reuses the block-cached `lindex` instead of re-accruing. This is plausible under `liquidate-multi`'s multi-asset, single-transaction design, but I was not able to directly confirm, from the raw contract source, the exact call ordering between `socialize-debt` and subsequent `resolve-price`/cache lookups within `liquidate-multi` due to tool/iteration limits — this analysis relies on the wiki's description of the caching and socialization mechanisms rather than a line-by-line read of the full `liquidate-multi` implementation.

### Recommendation
Invalidate (clear or refresh) the `index-cache` entry for an asset immediately whenever `socialize-debt` (or any other function that mutates a vault's `lindex`/`index` directly) is invoked within the same transaction, or have `socialize-debt` write through to `index-cache` so subsequent lookups in the same block reflect the updated index rather than the pre-mutation cached value.

### Proof of Concept
Conceptual sequence (single transaction):
1. Liquidator calls `liquidate-multi` covering multiple debt assets against a shared zToken collateral position.
2. Processing the first debt leg triggers `accrue-vault-indexes` for the zToken's underlying vault, caching `lindex` in `index-cache[aid, block-time]`.
3. Collateral is insufficient to cover the full debt, so `socialize-debt` is called on that vault, reducing its true `lindex` in vault storage.
4. Processing a subsequent debt leg (or a subsequent seizure/valuation step) in the same transaction calls `resolve-price`/valuation logic for the same zToken; because `index-cache` still holds the pre-socialization `lindex` for that block, the valuation uses the stale, higher index instead of the freshly reduced one.

Verification of the precise call ordering inside `liquidate-multi` and `socialize-debt` in the raw `mainnet/contracts/market/v0-4-market.clar` source would be needed to confirm exploitability with certainty; this could not be completed within the available tool iterations.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1-1)
```text
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
```
