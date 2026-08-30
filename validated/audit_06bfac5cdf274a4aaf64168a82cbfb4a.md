### Title
Disabled-collateral value is folded into the enabled-only collateral aggregate before the withdrawal health check, letting a disabled asset "borrow" the wrong egroup's LTV haircut - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`collateral-remove` computes `collateral-value` from `get-notional-evaluation`, which is deliberately restricted to the **enabled-collateral mask** (`position-mask`, sourced from `get-position`, which itself is built from `get-enabled-bitmap`). This is the aggregate the rest of the protocol (`borrow`, `liquidate`, `get-liquidation-position`) treats as "the" risk-relevant collateral value for a position. When the asset being withdrawn is a *disabled* collateral (`is-collateral-enabled` false — i.e. explicitly excluded from the risk-relevant aggregate, analogous to an "excluded deposit"), the function nonetheless re-injects that excluded asset's full spot-priced notional value (`disabled-notional`) back into the same `collateral-value` variable to form `total-collateral-value`, and runs the withdrawal health check through `is-healthy` using `current-ltvb` — the LTV-BORROW parameter of the egroup that was resolved *without* this asset. This exactly mirrors the reported class of bug: a value that was intentionally excluded from an aggregate (excluded FEI deposits vs. `userCirculatingFei`/PCV) gets folded back into that same aggregate and evaluated with parameters that were never computed to account for it, distorting the resulting ratio.

### Finding Description
In `collateral-remove` (mainnet/contracts/market/v0-4-market.clar, has-debt branch): [1](#0-0) 

- `position-mask = (get mask position)` and `assets = (get-assets position-mask)` are derived from `get-position`, which filters strictly to the enabled bitmap: [2](#0-1) 
- `collateral-value` (from `get-notional-evaluation`) is therefore, by construction, the risk-relevant aggregate that **excludes** any disabled asset - the same role `_protocolControlledFei`/PCV plays by excluding excluded deposits in the reported analog.
- `current-ltvb` is the `LTV-BORROW` of the egroup resolved from that same enabled-only `position-mask` — it was never computed with the disabled asset as a member.
- When the asset being removed is disabled, the code computes `disabled-notional` from the **full** balance of that asset (`pos-full`, unrestricted by the enabled mask) at its live spot price, with **no LTV haircut of its own**, and adds it directly into the enabled-only aggregate:
```
(total-collateral-value (+ collateral-value disabled-notional))
...
(is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb)
```
- `is-healthy` then applies the *enabled-egroup's* LTV to this contaminated sum: [3](#0-2) 

The disabled asset was excluded from the mask/egroup precisely because its risk parameters are not to be counted the same way as enabled collateral (it may have no LTV entry, may be disabled due to a problematic oracle/asset, etc.). Folding its raw notional into the enabled aggregate and then applying an unrelated egroup's LTV to the sum silently substitutes an arbitrary risk parameter for an asset the protocol elsewhere (borrow, liquidation via `get-liquidation-position`/`get-position`) treats as contributing **zero** to the risk-relevant collateral value.

### Impact Explanation
Because every other risk check in the protocol (`borrow`'s `get-position`/`get-egroup`, `liquidate`'s `get-liquidation-position`) only counts enabled collateral, the amount of the disabled asset left in the position after a partial withdrawal is *not* counted again once the withdrawal succeeds. The one-time health check in `collateral-remove` therefore permits a user to withdraw more of their genuinely risk-recognized collateral than the protocol's own liquidation/borrow logic would consider safe, because it temporarily credited the disabled asset's un-haircut, spot-priced value against the enabled egroup's LTV threshold. This can leave a position under-collateralized relative to what the protocol's liquidation engine will ever actually recognize, understating risk and enabling withdrawals that push positions toward unrecoverable bad debt — a temporary/permanent freezing-of-funds and insolvency-adjacent risk consistent with the reported bug class (skewed collateralization ratio from wrongly-included excluded value).

### Likelihood Explanation
This path triggers on any ordinary `collateral-remove` call for a disabled collateral asset while the account has debt — no privileged action or governance compromise is required; a user simply needs to hold a disabled-collateral asset and debt simultaneously, which is a normal, easily reachable state (assets can be disabled by admin action for reasons unrelated to the specific user's position).

### Recommendation
Do not add the disabled asset's spot-priced notional into the enabled-only `collateral-value` aggregate under the enabled egroup's LTV. Either (a) evaluate the disabled asset's contribution under its own (or a conservative/zero) LTV independently of `current-ltvb`, or (b) require that the post-removal check be performed strictly on the enabled-collateral aggregate, disallowing use of disabled-asset value as backstop collateral, consistent with how `borrow` and `liquidate` treat it.

### Proof of Concept
1. Admin disables collateral status for asset `X` (`is-collateral-enabled = false`), while user Alice already holds a large balance of `X` as collateral plus other enabled collateral and outstanding debt.
2. Alice calls `collateral-remove` for a small `amount` of `X`.
3. `position-mask`/`collateral-value` excludes `X` entirely (enabled-mask only), but the disabled branch computes `disabled-notional` from Alice's **full** `X` balance at spot price and forms `total-collateral-value = collateral-value + disabled-notional`.
4. `is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb` passes because `X`'s full value (haircut only by the unrelated enabled-egroup LTV) inflates the check, even though `X`'s remaining balance will never again be counted toward Alice's collateral in `borrow`/`liquidate` checks.
5. Alice repeatedly withdraws small amounts of `X` (or other enabled collateral, using `X`'s residual value as continued backstop) until her position's liquidation-recognized collateral (enabled-only) is insufficient to cover her debt, while every individual `collateral-remove` call passed its health check due to the phantom `X` valuation.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1120-1153)
```text
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
```

**File:** local-testing/contracts/market/market.clar (L488-490)
```text
(define-private (get-position (account principal)) ;; enabled only
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .market-vault get-position account mask)))
```

**File:** local-testing/contracts/market/market.clar (L678-681)
```text
(define-private (is-healthy (collateral-usd uint) (debt-usd uint) (ltv uint))
  (if (is-eq debt-usd u0)
      true
      (<= (* debt-usd BPS) (* collateral-usd ltv))))
```
