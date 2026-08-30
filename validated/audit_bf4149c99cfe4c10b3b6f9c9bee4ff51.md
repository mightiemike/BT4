### Title
Dust debt left unpaid because `is-healthy` treats rounded-to-zero USD debt as fully healthy - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`collateral-remove` (and the analogous `borrow`/`liquidate` health-gates) in `v0-4-market.clar` decide whether a withdrawal is allowed purely from USD-normalized values. The debt USD value is computed by `get-notional-evaluation`/`get-asset-value`, which floors (`normalize(... round-up=false)`) the token amount × price to base-currency precision. When a user's *actual* scaled debt in the debt token is small enough that its USD value rounds down to `u0`, `is-healthy` short-circuits to `true` unconditionally, letting the user withdraw 100% of their collateral while a non-zero debt balance remains on-chain forever.

### Finding Description
`is-healthy` treats any position with `debt-usd == u0` as trivially healthy: [1](#0-0) 

`collateral-remove` uses exactly this check, gated only on `has-debt = (> (len (get debt position)) u0)` (i.e., whether any debt *entry* exists, not whether its USD value is non-zero): [2](#0-1) 

The debt USD figure fed into that check comes from `get-notional-evaluation`, which for each debt entry calls `get-asset-value`/`find-and-resolve-asset-value`, normalizing `amount * price` down to base-currency decimals with `round-up = false`: [3](#0-2) [4](#0-3) 

Because this normalization always rounds down, a small enough residual scaled-debt balance (e.g. dust left after a `repay` that doesn't zero the position exactly) can produce `debt-usd = u0` even though `get-account-scaled-debt`/`debt-remove-scaled` in `v0-market-vault.clar` still records a non-zero scaled debt for that asset: [5](#0-4) 

When `debt-usd` is `u0`, `is-healthy` returns `true` regardless of `collateral-usd`, so `collateral-remove`'s `asserts! (is-healthy collateral-value debt-value current-ltvb) ERR-UNHEALTHY` and the subsequent post-removal health check both pass trivially, letting the full collateral be withdrawn while the dust debt entry (and its mask bit) remains outstanding indefinitely, uncollateralized.

### Impact Explanation
This is a temporary/permanent freezing-of-funds-adjacent loss for the protocol rather than the user: the residual debt token amount is never repaid (borrower has no more collateral backing it and no economic incentive to repay), so the vault permanently loses that principal. Accumulated over many users/assets over time this becomes uncollateralized bad debt sitting on vault balance sheets — a protocol-insolvency-class impact, matching the report's classification of small-but-compounding dust losses.

### Likelihood Explanation
Likelihood is high: any borrower can trivially trigger this by repaying down to just above the USD-rounding threshold (a few token base units, depending on price/decimals) and then calling `collateral-remove` for their full collateral amount in a single transaction — no coordination with other users or privileged access is required, and the flow is a normal, expected user journey (`repay` then `collateral-remove`).

### Recommendation
In `collateral-remove` (and analogously in `borrow`/`liquidate`), do not rely solely on the USD-denominated `debt-usd == u0` shortcut in `is-healthy` to allow full withdrawal. Before permitting `removing-all`/full collateral exit, additionally verify that all debt entries have `scaled == u0` (i.e., check the raw debt list from `position`/`pos-full`, not only its USD valuation) so that a token-denominated non-zero debt can never be bypassed by a USD-rounding artifact.

### Proof of Concept
1. Borrower supplies collateral and borrows a debt asset via `borrow`, establishing a debt entry with `scaled > 0` in `market-vault`/`v0-market-vault`.
2. Borrower calls `repay` for `debt_amount - dust`, where `dust` is chosen (based on the debt asset's price/decimals) so that the remaining scaled debt, when run through `get-asset-value`/`normalize(..., round-up=false)` in `get-notional-evaluation`, yields `debt-usd = u0`, while the on-chain scaled-debt entry is still `> u0` (verified via `market-vault`'s debt-scaled getter).
3. Borrower calls `collateral-remove` for their entire collateral balance. `has-debt` is `true` (debt list non-empty) but `debt-value` computed by `get-notional-evaluation` is `u0`.
4. `is-healthy(collateral-value, u0, current-ltvb)` returns `true` unconditionally (`mainnet/contracts/market/v0-4-market.clar:656-659`), so both the pre- and post-removal health asserts pass, and `collateral-remove` succeeds, sending 100% of the collateral back to the borrower.
5. The borrower now holds zero collateral but still has a non-zero `scaled` debt entry recorded in `market-vault`, which is never repaid — the dust principal is a permanent loss to the vault's liquidity pool.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L656-659)
```text
(define-private (is-healthy (collateral-usd uint) (debt-usd uint) (ltv uint))
  (if (is-eq debt-usd u0)
      true
      (<= (* debt-usd BPS) (* collateral-usd ltv))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L668-676)
```text
(define-private (find-and-resolve-asset-value
                  (assets (list 64 
                    { id: uint, addr: principal, decimals: uint,
                    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
                    collateral: bool, debt: bool, price: uint }))
                  (asset-id uint) (amount uint) (round-up bool))
  (match (find-asset asset-id assets)
    asset (normalize (* amount (get price asset)) (get decimals asset) round-up)
    u0))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L678-688)
```text
;; find-and-resolve-asset-value has "price" already pre-calculated, get-asset-value does not
(define-private (get-asset-value
                  (asset { id: uint, addr: principal, decimals: uint,
                          oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
                          collateral: bool, debt: bool})
                  (amount uint) (round-up bool))
    (let ((oracle-data (get oracle asset))
          (price (try! (price-resolve oracle-data)))
          (decimals (get decimals asset)))
      (ok (normalize (* amount price) decimals round-up))))

```

**File:** mainnet/contracts/market/v0-4-market.clar (L1111-1136)
```text
        (account contract-caller)
        (collateral-receiver (match receiver recv recv contract-caller))
        (position (try! (get-position account)))
        (has-debt (> (len (get debt position)) u0)))

    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (if has-debt
        ;; HAS DEBT: Full flow with price resolution and health checks
        (let ((is-collateral-enabled (get collateral asset))
              (feeds-check (try! (write-feeds price-feeds)))
              (position-mask (get mask position))
              (pos-full (if is-collateral-enabled position (try! (get-full-position account))))
              (u-debt (accrue-user-debts (get debt pos-full)))
              (u-coll (accrue-user-collateral (get collateral pos-full)))
              (assets (get-assets position-mask))
              (curr-coll-aid (find-collateral-amount (get collateral position) asset-id))
              (removing-all (is-eq amount curr-coll-aid))
              (current-group (try! (get-egroup position-mask)))
              (current-ltvb (buff-to-uint-be (get LTV-BORROW current-group)))
              (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
              (collateral-value (get collateral notional-valued-assets))
              (debt-value (get debt notional-valued-assets))
              (removed-asset-value (find-and-resolve-asset-value assets asset-id amount true)))

          (asserts! (is-healthy collateral-value debt-value current-ltvb) ERR-UNHEALTHY)
```

**File:** mainnet/contracts/market/v0-market-vault.clar (L473-480)
```text
(define-public (debt-remove-scaled (account principal) (scaled-amount uint) (asset-id uint))
  (let ((states (var-get pause-states))
        (entry (resolve account))
        (user-id (get id entry))
        (mask (get mask entry))
        (remaining (try! (remove-user-scaled-debt user-id asset-id scaled-amount)))
        (nmask (if (is-eq remaining u0)
                      (mask-update mask asset-id false false) ;; debt, remove
```
