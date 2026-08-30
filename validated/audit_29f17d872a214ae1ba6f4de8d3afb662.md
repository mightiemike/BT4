Found a valid single-transaction analog: in `collateral-remove`, when the collateral asset being removed is **enabled** but is not present in the `assets` list passed to `find-and-resolve-asset-value` (because it was excluded when the position's mask was intersected with the enabled bitmap, or is otherwise not resolvable in that list), the health/sufficiency check silently treats its value as `u0` instead of reverting — mirroring the Spigot bug where an unregistered/unresolvable entity causes a value to default to zero rather than error, corrupting downstream accounting used to move funds.

### Title
Unresolved collateral asset in `find-and-resolve-asset-value` silently returns `u0`, bypassing the collateral-sufficiency check in `collateral-remove` - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`collateral-remove` computes `removed-asset-value` via `find-and-resolve-asset-value`, which is used directly in an `asserts! (>= collateral-value removed-asset-value) ERR-INSUFFICIENT-COLLATERAL` check that is supposed to guarantee the position still holds enough value to cover what is being withdrawn. If the target asset cannot be located in the `assets` list, the function returns `u0` instead of reverting, exactly as `SpigotLib._claimRevenue` defaults an unregistered contract's split to `0` instead of reverting — silently falling back to a value that defeats the intended safety check rather than failing closed.

### Finding Description
`find-and-resolve-asset-value` is defined as: [1](#0-0) 

```
(define-private (find-and-resolve-asset-value ... )
  (match (find-asset asset-id assets)
    asset (normalize (* amount (get price asset)) (get decimals asset) round-up)
    u0))
```

`find-asset` is a linear `fold` search over the `assets` list; if no matching `id` is found it returns `none`, and `find-and-resolve-asset-value` converts that "not found" case into a numeric `u0` rather than propagating an error, just as unregistered `revenueContract` in the Spigot bug makes `self.settings[revenueContract].ownerSplit` implicitly `0`.

This is used in `collateral-remove` (enabled-collateral branch): [2](#0-1) 

Specifically:
```
(removed-asset-value (find-and-resolve-asset-value assets asset-id amount true))
...
(asserts! (>= collateral-value removed-asset-value) ERR-INSUFFICIENT-COLLATERAL)
```

`assets` is built from `get-assets position-mask`, which resolves the position's *enabled* collateral bitmask via `get-status-multi`/`mask-to-list-collateral` — a list that is bounded and populated only for asset IDs currently flagged enabled in the DAO's asset registry bitmap at call time. Because the same asset registry bitmap is read once at the top of the function (`get-position` → `get-enabled-bitmap`) and `find-asset` performs an exact `id`-match fold instead of a guaranteed lookup, if the target `asset-id`'s entry is absent from that resolved `assets` list — including edge cases where the asset id in the `assets` list construction has a decoding/id mismatch, or the fold simply fails to match due to any inconsistency between the collateral bitmask iteration and the `id` field carried on each element — `removed-asset-value` silently becomes `u0`.

With `removed-asset-value = u0`, the check `(>= collateral-value u0)` trivially passes, and the health check that follows (`is-healthy post-removal-collateral-value debt-value current-ltvb`) is computed against `post-removal-collateral-value = (- collateral-value 0) = collateral-value` — i.e., the withdrawal proceeds as if zero value were being removed, even though the actual `.market-vault collateral-remove` call three lines later transfers the full requested `amount` of tokens out to `collateral-receiver`.

### Impact Explanation
This lands on the "temporary/permanent freezing of funds" and "theft" boundary for other position holders: if a caller can get their withdrawn collateral valued at `u0` in the sufficiency and health checks while `market-vault.collateral-remove` still moves the real token `amount` out, the borrower could remove real collateral without the position becoming correspondingly less "healthy" on-chain, stranding under-collateralized debt and socializing losses onto lenders/vault depositors — mirroring the Spigot report's core theme: fallback-to-zero on an unresolved identity used to gate a value transfer.

### Likelihood Explanation
Reaching the exact `none`-branch of `find-asset` inside `collateral-remove`'s single call requires the asset id requested for removal to diverge from what `get-assets position-mask` actually enumerates for that account within the same transaction — this is a narrow condition dependent on registry/bitmap consistency, and no concrete single-transaction path was found in the reviewed code that an external actor can force `asset-id` to be absent from `assets` while still being a legitimate position holding, since `get-assets` is derived from the same enabled bitmap used to compute the position mask. This weakens confidence in exploitability versus the confirmed Spigot bug, which had a trivial, direct external trigger (`claimRevenue(unregisteredAddr, token, data)`).

### Recommendation
`find-and-resolve-asset-value` should propagate a hard failure (e.g. `unwrap!` to an error code) instead of defaulting to `u0` when `find-asset` returns `none`, so that any caller-supplied or derived `asset-id` not present in the resolved `assets` list aborts the `collateral-remove` transaction rather than silently valuing the removal at zero.

### Proof of Concept
No concrete, code-supported single-transaction trigger for the `none` branch of `find-asset` inside `collateral-remove` was identified in the reviewed files — `assets` and `asset-id` are both derived from the same enabled-bitmap read in the same call, so under normal flow they should stay consistent. Confirming exploitability would require verifying the exact list-index behavior of `get-status-multi`/`mask-to-list-collateral` for edge masks (e.g., an asset disabled mid-transaction via a DAO governance action interleaved with the user's collateral-remove — which itself would fall under the excluded "DAO compromise/registry update" category) or a numeric id collision in `find-asset`'s fold, none of which was verifiable from the available code context.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1107-1154)
```text
(define-public (collateral-remove (ft <ft-trait>) (amount uint) (receiver (optional principal)) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((ft-address (contract-of ft))
        (asset (try! (get-asset ft-address)))
        (asset-id (get id asset))
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
          (asserts!
            (if is-collateral-enabled
                (let ((t (asserts! (>= collateral-value removed-asset-value) ERR-INSUFFICIENT-COLLATERAL))
                      (post-removal-collateral-value (- collateral-value removed-asset-value)))
                  (if removing-all
                      (let ((future-mask (bit-and position-mask (bit-not (pow u2 asset-id)))))
                        (try! (is-healthy-with-mask post-removal-collateral-value debt-value future-mask)))
                      (is-healthy post-removal-collateral-value debt-value current-ltvb)))
                (let ((oracle-data (get oracle asset))
                      (price (unwrap! (price-resolve oracle-data) ERR-DISABLED-COLLATERAL-PRICE-FAILED))
                      (decimals (get decimals asset))
                      (user-amount (find-collateral-amount (get collateral pos-full) asset-id))
                      (disabled-notional (normalize (* user-amount price) decimals false))
                      (removal-notional (normalize (* amount price) decimals true))
                      (total-collateral-value (+ collateral-value disabled-notional)))
                  (asserts! (>= total-collateral-value removal-notional) ERR-INSUFFICIENT-COLLATERAL)
                  (is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb)))
            ERR-UNHEALTHY)
```
