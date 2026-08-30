### Title
Attacker can borrow against a favorable-but-stale Pyth price submitted in the same transaction, passing the health check and leaving bad debt - (File: `local-testing/contracts/market/market.clar` / `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`borrow` (and `collateral-add`/`collateral-remove`/`liquidate`) accept an optional `price-feeds` parameter that lets the caller push a fresh signed Pyth update atomically, in the same transaction, right before the health check runs. The staleness guard `oracle-timestamp-fresh` only requires the submitted price's timestamp to be `>=` the previously stored timestamp and within `max-staleness` of `stacks-block-time` — it never requires the price to be the *most current* one available. A borrower can therefore choose, from the set of still-"fresh" (within `max-staleness`) valid Pyth VAAs, whichever historical price is most favorable to their position, submit it in-band with `borrow`, pass the pre- and post-borrow health checks computed against that favorable price, extract the maximum debt the (stale) price allows, and abandon/under-collateralize the position once the real/current price is later written on-chain by anyone else.

### Finding Description
`write-feeds`/`price-resolve` in `market.clar` resolve the price to use for `get-assets`/health-check math: [1](#0-0) 

`oracle-timestamp-fresh` only enforces monotonicity vs. the last stored update and a maximum age relative to *now*, not "is this the latest price obtainable right now": [2](#0-1) 

`borrow` calls `write-feeds price-feeds` (the attacker-supplied, in-band Pyth update) as the very first step, before the position/mask is even loaded, and then computes both the pre-borrow and post-borrow health checks using whatever price was just written: [3](#0-2) [4](#0-3) 

This is the same root-cause pattern as Sherlock M-8: in the referenced report, the ClearingHouse's `isValidSender` let anyone settle a trade directly, and the price used to size/settle that trade could be pushed away from the "true" price within the tolerated band, letting the attacker extract value/create bad debt before the price reverted. Here, the "price band tolerance" is `max-staleness`/monotonicity instead of a UniV3 pool + oracle price-band check, and the "self-sandwich" is replaced by the attacker choosing which valid-but-not-current Pyth update to push in-band immediately before the health check that gates their own borrow. As the sponsor (`paco0x`) noted for the original finding, "the exploiter's actual source of income is that he increased his buying power by using a stale price for opening position... he'll be in bad debt after the price updated" — the identical mechanism applies to `borrow`'s in-band `price-feeds` update.

The `write-feeds`/mutation (pushing a favorable stale price) happens, then the guard (`is-healthy` / `is-healthy-with-mask`) is evaluated against that mutated cached price, all within one transaction and controlled entirely by the borrower — no second party is required.

### Impact Explanation
A borrower can obtain more debt than the true current collateral value supports. When the genuine/current price is later written on-chain (by the next legitimate caller's in-band update, a keeper, or any other user), the position becomes under-collateralized/insolvent bad debt that is ultimately socialized across the protocol (see the bad-debt-socialization logic in `liquidate`), i.e. protocol insolvency — a Critical-class impact per the accepted impact list, though the achievable magnitude is bounded by how large a price divergence can exist within a single `max-staleness` window (analogous to the Medium severity ultimately assigned to M-8, since the "sandwich" requires a real, momentary price divergence to be profitable).

### Likelihood Explanation
Requires: (1) `max-staleness` configured wide enough that meaningfully different valid Pyth prices exist within the window (any non-trivial staleness tolerance, since Pyth price updates continuously), and (2) a real price move during that window that the attacker can time their `borrow` call and in-band `price-feeds` submission to exploit. This mirrors the "large gap during a short staleness window" precondition that Sherlock ultimately accepted as valid at Medium severity for M-8 — external condition-dependent but not implausible, and entirely self-triggered/single-transaction.

### Recommendation
- Require the submitted `price-feeds` update's timestamp to be within a much tighter tolerance of `stacks-block-time` (not just `<= max-staleness`), or require it to strictly improve freshness relative to the currently stored price rather than merely being "not older."
- Consider disallowing in-band price updates from moving the *effective* collateral/debt valuation in the borrower's favor beyond what the previously stored (already-validated) price implied, i.e., use `min`/`max` of old vs. new price depending on whether it's collateral or debt being valued, when the update is submitted by the transaction's own beneficiary.
- Add a price-band-style sanity check between the previous stored price and the newly submitted in-band price for hot paths (`borrow`, `collateral-add`) similar to the `check-confidence` guard already used for Pyth confidence intervals.

### Proof of Concept
1. Configure `max-staleness` for an asset's Pyth feed to a non-trivial window (e.g. 60s), as is done in the test setup (`proposalSetPriceStaleness`).
2. Wait for a real market price move within that window such that an earlier, still-"fresh" (per `oracle-timestamp-fresh`) signed Pyth VAA values the borrower's collateral higher than the current true price, or values the debt asset lower.
3. Attacker calls `market.borrow(debt-ft, amount, receiver, (some (list <old-but-valid-VAA>)))`.
4. `write-feeds` (line 1267) writes the attacker-chosen VAA via `price-resolve` (lines 395-417), passing `oracle-timestamp-fresh` because `ts >= prev` and `stacks-block-time - ts <= max-staleness`.
5. `is-healthy` / `is-healthy-with-mask` (lines 1295, 1310) are evaluated against this favorable price, allowing a larger `amount` to be borrowed than the true current price would permit.
6. Once a subsequent transaction (anyone's) writes the true/current Pyth price, the borrower's position is undercollateralized; they abandon it, and bad debt is later socialized via the `liquidate` flow's `bad-debt-socialized` path.

### Citations

**File:** local-testing/contracts/market/market.clar (L387-417)
```text
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        (ident (get ident data))
        (key { type: type, ident: ident })
        (resolution (try! (resolve-price-feed type ident)))
        (price (get value resolution))
        (callcode (get callcode data))
        (final-price (try! (resolve-callcode price callcode)))
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))

    ;; validate price and timestamp using max-staleness from oracle data
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```

**File:** local-testing/contracts/market/market.clar (L1261-1295)
```text
(define-public (borrow (ft <ft-trait>) (amount uint) (receiver (optional principal)) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((address (contract-of ft))
        (asset (try! (get-asset address)))
        (asset-id (get id asset))
        (account contract-caller)
        (funds-receiver (match receiver recv recv contract-caller))
        (feeds-check (try! (write-feeds price-feeds)))
        
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
```

**File:** local-testing/contracts/market/market.clar (L1297-1312)
```text
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
```
