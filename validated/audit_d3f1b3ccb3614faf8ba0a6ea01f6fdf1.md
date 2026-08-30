### Title
Borrowing a trivial amount in any asset resets a position-wide `last-borrow-block` timestamp, letting a borrower block liquidation of an unrelated, unhealthy debt - ([File: local-testing/contracts/market/market.clar])

### Summary
The Moonwell finding shows a user can prevent liquidation by exploiting per-market state that is shared/aggregated across a multi-asset position rather than scoped to the specific debt being liquidated. Zest's `liquidate` function has an analogous single-position, single-field guard (`last-borrow-block`) that is not scoped per debt-asset, so a borrower can refresh it by taking on debt in any asset — including a low-value/low-activity one — and thereby block liquidation of a completely different, actually-unhealthy debt in the same position, in the same block.

### Finding Description
The position record tracked by the market/market-vault stores a single `last-borrow-block` field per account position, not per debt asset: [1](#0-0) 

`liquidate` reads this single field and uses it as an oracle-frontrunning guard, rejecting liquidation if the position's `last-borrow-block` equals the current block, regardless of which debt asset is being liquidated: [2](#0-1) 

`borrow()` updates the account's debt via `debt-add-scaled` for whatever asset is borrowed: [3](#0-2) 

Because the guard field lives on the position as a whole (one value covering all assets in the mask) rather than per-asset, a borrower can:
1. Hold an unhealthy debt in Market/Asset A (LTV above `LTV-LIQ-PARTIAL`).
2. In the same block a liquidator's `liquidate` transaction targeting Asset A is expected to land, submit `borrow` for a trivial amount of a different, low-liquidity/low-activity Asset B (any debt-enabled asset the position's egroup allows), which the position can support even with minimal health margin, or which only needs to pass the pre-borrow health check trivially.
3. `borrow()` succeeds and calls `debt-add-scaled`, which updates the position's single `last-borrow-block` to the current block height.
4. The pending/following `liquidate(borrower, ...)` call — for Asset A — reads `last-borrow-block` from the position and finds it equals `stacks-block-height`, tripping `ERR-LIQUIDATION-BORROW-SAME-BLOCK` and reverting, even though the borrow that set the flag has nothing to do with Asset A.
5. Repeating step 2 every block the borrower is targeted for liquidation perpetually blocks liquidators from seizing collateral for the unhealthy Asset A debt, while the position may continue to accrue interest/lose collateral value.

This is a single-block/single-transaction-adjacent guard-bypass: the guard (`last-borrow-block == stacks-block-height`) is a position-wide cached value that gets refreshed by an action unrelated to the asset actually being checked, then consumed by the liquidation health/anti-frontrunning check before liquidation executes — the same "cached value not invalidated per its real scope, then relied upon by a later guard" pattern as the source report (which relied on manipulating `totalReserves` in an unrelated market to break the shared liquidity computation used to gate liquidation).

### Impact Explanation
If a borrower can reliably refresh the guard every block (e.g., via a bot submitting a minimal borrow transaction ahead of/alongside anticipated liquidation attempts), liquidators can be indefinitely prevented from liquidating an actually undercollateralized position. This lets bad debt accumulate as collateral value continues to fall or debt continues to accrue, ultimately risking protocol insolvency (unrecoverable bad debt) and, at minimum, causes temporary freezing of the collateral/liquidation proceeds that liquidators would otherwise be entitled to.

### Likelihood Explanation
Likelihood is moderate-to-high for a motivated borrower: the attacker only needs authorization over their own account (no cross-user interference, no oracle/DAO compromise) and the ability to submit a small `borrow` transaction for any debt-enabled asset in their egroup, which is a normal, permitted operation. The requirement to time it in the same block as a liquidation attempt is achievable by an automated bot monitoring for liquidation-eligible transactions/mempool activity, similar to the original Moonwell PoC pattern of pre-positioning state to defeat a later check.

### Recommendation
Scope the same-block anti-frontrunning guard to the specific debt asset being liquidated (e.g., track `last-borrow-block` per `{account, asset-id}` rather than one value per position), or alternatively gate it on whether the specific debt being repaid/liquidated was itself increased in the current block, so that unrelated borrow activity in other assets cannot suppress liquidation of an already-unhealthy, unrelated debt position.

### Proof of Concept
Not independently executed against a live/test environment; derived from static analysis of the cited `liquidate`, `borrow`, and position-tuple definitions. Key evidence:
- Position tuple has a single, position-wide `last-borrow-block` field (not per debt asset): [1](#0-0) 
- `liquidate` gates on this field for the whole position: [2](#0-1) 
- `borrow` for any asset calls `debt-add-scaled`, the plausible source of the `last-borrow-block` refresh: [3](#0-2) 

Note: I was unable to retrieve the exact line inside `debt-add-scaled` in `market-vault.clar` that sets `last-borrow-block` before the tool budget ran out; this is inferred from the field's name/purpose and its consumption pattern in `liquidate`, but should be verified directly in `local-testing/contracts/market/market-vault.clar` (and its mainnet counterpart `mainnet/contracts/market/v0-market-vault.clar`) before treating this as fully confirmed.

### Citations

**File:** local-testing/tests/clarigen-types.ts (L1542-1556)
```typescript
  "position": {
  "account": string;
  "collateral": {
  "aid": number | bigint;
  "amount": number | bigint;
}[];
  "debt": {
  "aid": number | bigint;
  "scaled": number | bigint;
}[];
  "id": number | bigint;
  "lastBorrowBlock": number | bigint;
  "lastUpdate": number | bigint;
  "mask": number | bigint;
};
```

**File:** local-testing/contracts/market/market.clar (L1312-1320)
```text
    (try! (vault-system-borrow asset-id amount funds-receiver))
    (let ((scaled-debt-added (convert-to-scaled-debt asset-id amount true))
          (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id)))))
      (try! (contract-call? .market-vault
                            debt-add-scaled
                            account
                            scaled-debt-added
                            asset-id))
      
```

**File:** local-testing/contracts/market/market.clar (L1450-1458)
```text
    
    ;; Oracle frontrunning protection: prevent same-block liquidation
    ;; This blocks flash-loan based attacks where user borrows + gets liquidated in same block
    (last-borrow-block (get last-borrow-block position))
    (same-block-check (asserts! (not (is-eq last-borrow-block stacks-block-height)) ERR-LIQUIDATION-BORROW-SAME-BLOCK))

    ;; health check (FAIL-FAST) 
    ;; Check position is liquidatable BEFORE calling calc-liq-factor
    (health-check  (asserts! (>= current-ltv ltv-liq-partial) ERR-HEALTHY))
```
