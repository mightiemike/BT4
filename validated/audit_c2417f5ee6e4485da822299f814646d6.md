### Title
Spot positions in zero-weight (deprecated) products are permanently unliquidatable, forcing protocol to absorb bad debt via insurance/socialization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

When a spot product's `longWeightInitialX18` is set to `0` (the protocol's mechanism for deprecating a product), `_assertLiquidationAmount` explicitly blocks any liquidation of that product with `ERR_INVALID_PRODUCT`. At the same time, the health engine still iterates over the subaccount's balance in the zero-weight product and contributes `amount * 0 * price = 0` to health. A subaccount that holds a positive balance in a deprecated product and carries liabilities elsewhere is therefore permanently under maintenance health with no reachable liquidation path for the stranded collateral. The protocol is forced to drain insurance or socialize losses even though real collateral exists in the deprecated product.

---

### Finding Description

**Step 1 — Deprecation mechanism.**
`BaseEngine.updateRisk` (owner-only) allows setting `longWeightInitial = 0` and `longWeightMaintenance = 0` for any product. The only constraint enforced is `longWeightInitial <= longWeightMaintenance`, which `0 <= 0` satisfies. This is the intended path for deprecating a product. [1](#0-0) 

**Step 2 — Health contribution of a zero-weight product.**
`_calculateProductHealth` computes `amount * weight * price`. When `longWeightMaintenanceX18 == 0`, a positive balance in the deprecated product contributes exactly `0` to maintenance health. The subaccount's health is therefore determined solely by its other positions. If it has any net liability, it is under maintenance health. [2](#0-1) 

**Step 3 — Liquidation of the zero-weight product is hard-blocked.**
Inside `_assertLiquidationAmount`, the spot liquidation branch unconditionally reverts when `longWeightInitialX18 == 0`:

```solidity
} else if (engine == address(spotEngine)) {
    require(
        spotEngine.getRisk(spotId).longWeightInitialX18 != 0,
        ERR_INVALID_PRODUCT
    );
```

There is no bypass path analogous to Compound's `isDeprecated` shortcut. Any liquidator who targets the deprecated spot product is rejected. [3](#0-2) 

**Step 4 — Finalization silently skips the zero-weight balance.**
`_finalizeSubaccount` (triggered by `productId == type(uint32).max`) also skips zero-weight products in every asset-closure check:

```solidity
if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
    continue;
}
```

Finalization therefore succeeds while the deprecated collateral remains in the subaccount. The USDC deficit is covered by insurance or socialized across all depositors, even though the subaccount holds real (but unliquidatable) collateral. [4](#0-3) [5](#0-4) 

**Step 5 — Liability check also skips zero-weight products.**
`_assertCanLiquidateLiability` skips zero-weight spot products in its positive-balance check, so the stranded collateral does not even block the liability liquidation path for other products. [6](#0-5) 

---

### Impact Explanation

A subaccount that deposited into a product before it was deprecated ends up with:
- A positive balance in the zero-weight product (real value, but health contribution = 0).
- A USDC liability (or other liability) that makes it permanently under maintenance health.

No liquidator can touch the zero-weight collateral. The only exit is `_finalizeSubaccount`, which abandons the collateral in the subaccount and covers the USDC deficit from the insurance fund or via `socializeSubaccount`. This directly drains the insurance fund and/or dilutes all depositors' balances — a concrete, measurable asset loss to the protocol. [7](#0-6) 

---

### Likelihood Explanation

Product deprecation (setting weights to zero) is a normal, expected protocol governance action. Any user who held a position in a product at the time of deprecation and also carries liabilities is affected. The trigger requires no attacker — it is an emergent consequence of the interaction between the deprecation mechanism and the liquidation guard. Likelihood is **medium**: it requires a deprecation event, but such events are anticipated in the protocol's lifecycle.

---

### Recommendation

Mirror the Compound fix: add an `isDeprecated` check at the top of `_assertLiquidationAmount` (and `_assertCanLiquidateLiability`) that, when the product is deprecated (`longWeightInitialX18 == 0`), bypasses the `ERR_INVALID_PRODUCT` guard and allows the liquidator to seize the full balance. This ensures deprecated collateral can always be recovered to cover liabilities before insurance or socialization is invoked.

```solidity
// In _assertLiquidationAmount, before the ERR_INVALID_PRODUCT check:
bool isDeprecated = spotEngine.getRisk(spotId).longWeightInitialX18 == 0;
if (!isDeprecated) {
    require(
        spotEngine.getRisk(spotId).longWeightInitialX18 != 0,
        ERR_INVALID_PRODUCT
    );
}
// allow full balance liquidation for deprecated products
```

Similarly, `_finalizeSubaccount` should require that zero-weight spot balances are also zero before finalization proceeds (or liquidate them first), so that real collateral is never silently abandoned.

---

### Proof of Concept

1. Admin calls `spotEngine.updateRisk(productId, RiskStore{longWeightInitial: 0, longWeightMaintenance: 0, shortWeightInitial: X, shortWeightMaintenance: X, priceX18: P})` — product is now deprecated.
2. Alice holds `+100` units of the deprecated spot product and `-50 USDC` (liability). Her maintenance health = `100 * 0 * P + (-50) = -50 < 0`. She is under maintenance.
3. Liquidator calls `liquidateSubaccountImpl` with `productId = deprecatedId`, `amount = 100`.
4. `_assertLiquidationAmount` hits the `require(longWeightInitialX18 != 0)` check → reverts with `ERR_INVALID_PRODUCT`.
5. Liquidator instead calls `liquidateSubaccountImpl` with `productId = type(uint32).max` (finalize).
6. `_finalizeSubaccount` skips the deprecated product's balance check, covers the `-50 USDC` from insurance, and returns `true`. Alice's `+100` deprecated tokens remain in her subaccount, permanently stranded.
7. Insurance fund is reduced by 50 USDC despite Alice holding 100 units of real (but unliquidatable) collateral. [8](#0-7)

### Citations

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
    }
```

**File:** core/contracts/BaseEngine.sol (L278-290)
```text
    function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
        external
        onlyOwner
    {
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance,
            ERR_BAD_PRODUCT_CONFIG
        );

        _risk().value[productId] = riskStore;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L159-163)
```text
        } else if (engine == address(spotEngine)) {
            require(
                spotEngine.getRisk(spotId).longWeightInitialX18 != 0,
                ERR_INVALID_PRODUCT
            );
```

**File:** core/contracts/ClearinghouseLiq.sol (L235-245)
```text
        for (uint32 i = 1; i < spotIds.length; ++i) {
            uint32 spotId = spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_LIQUIDATABLE_LIABILITIES);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L301-311)
```text
        for (uint32 i = 1; i < v.spotIds.length; ++i) {
            uint32 spotId = v.spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L379-384)
```text
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L395-412)
```text
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
    }
```
