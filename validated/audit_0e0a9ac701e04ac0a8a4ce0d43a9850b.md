### Title
`updateRisk()` Missing Absolute Bounds on Maintenance Weight Fields Corrupts Health Calculations — (`File: core/contracts/BaseEngine.sol`)

---

### Summary

`BaseEngine.updateRisk()` enforces only the ordering relationship between initial and maintenance weights, but omits the absolute magnitude bounds that `_addOrUpdateProduct()` enforces on the same `RiskStore` fields. An owner can therefore set `longWeightMaintenance > 1e9` or `shortWeightMaintenance < 1e9`, placing maintenance weights outside the documented `[0, 2]` safe range and corrupting every downstream health calculation that uses those weights.

---

### Finding Description

`RiskHelper.RiskStore` documents that all four weight fields are "between 0 and 2 … times 1e9": [1](#0-0) 

`_addOrUpdateProduct()` enforces this with explicit absolute bounds: [2](#0-1) 

The two critical lines are:
- `longWeightMaintenance <= 10**9` — long maintenance weight must not exceed 1.0
- `shortWeightMaintenance >= 10**9` — short maintenance weight must not fall below 1.0

`updateRisk()`, the post-deployment path for adjusting risk parameters, checks only the ordering invariant and omits both absolute bounds entirely: [3](#0-2) 

This is a direct structural parallel to the reported `AMMGovernance` bug: a lower-bound check exists (`longWeightInitial <= longWeightMaintenance`) but the upper-bound check (`longWeightMaintenance <= 10**9`) is absent.

---

### Impact Explanation

Every subaccount health evaluation flows through `_calculateProductHealth`, which multiplies the stored weight directly into the health sum: [4](#0-3) 

`_risk()` scales the stored `int32` weight by `1e9` before use: [5](#0-4) 

If `longWeightMaintenance` is set to `2e9` (2.0) via `updateRisk`, a long position's health contribution is doubled relative to its actual collateral value. Subaccounts that are genuinely undercollateralized will report non-negative maintenance health, suppressing liquidations that should fire. Conversely, setting `shortWeightMaintenance` below `1e9` reduces the health penalty for short positions, again masking insolvency. Both paths lead to the protocol accumulating bad debt that the insurance fund must absorb.

---

### Likelihood Explanation

`updateRisk` is the routine operational path for adjusting risk parameters after deployment — it is expected to be called regularly as market conditions change. The missing bounds are not obvious from the function's own require statement; an operator comparing only to `_addOrUpdateProduct` would need to notice the discrepancy. A misconfiguration during a routine risk parameter update is a realistic operational error, matching the same class of mistake the original report describes for `emaAlpha`.

---

### Recommendation

Mirror the absolute bounds from `_addOrUpdateProduct` into `updateRisk`:

```solidity
require(
    riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
        riskStore.longWeightMaintenance <= 10**9 &&          // add
        riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance &&
        riskStore.shortWeightMaintenance >= 10**9,           // add
    ERR_BAD_PRODUCT_CONFIG
);
```

Additionally, document the expected safe operating range for each `RiskStore` field in the NatSpec of both `updateRisk` and `addOrUpdateProduct`, and enforce the same validation in any future governance path that writes to `_risk()`.

---

### Proof of Concept

1. Owner calls `SpotEngine.updateRisk(productId, RiskStore{ longWeightInitial: 9e8, longWeightMaintenance: 2e9, shortWeightInitial: 2e9, shortWeightMaintenance: 5e8, priceX18: ... })`.
2. The require at lines 282–286 passes: `9e8 <= 2e9` ✓ and `2e9 >= 5e8` ✓. No revert.
3. `_risk().value[productId]` is written with `longWeightMaintenance = 2e9`.
4. A user holds a long position worth 100 USDC at current price. `_calculateProductHealth` computes `amount.mul(2e18).mul(priceX18)` — reporting 200 USDC of maintenance health instead of 100 USDC.
5. The subaccount can now borrow an additional 100 USDC against phantom collateral. `Clearinghouse.getHealth(..., MAINTENANCE)` returns ≥ 0, blocking any liquidation attempt.
6. If price drops 50 %, the position is insolvent but still reports zero maintenance health; the protocol absorbs the loss. [3](#0-2) [2](#0-1)

### Citations

**File:** core/contracts/libraries/RiskHelper.sol (L14-24)
```text
    struct RiskStore {
        // these weights are all
        // between 0 and 2
        // these integers are the real
        // weights times 1e9
        int32 longWeightInitial;
        int32 shortWeightInitial;
        int32 longWeightMaintenance;
        int32 shortWeightMaintenance;
        int128 priceX18;
    }
```

**File:** core/contracts/BaseEngine.sol (L55-58)
```text
        r.longWeightInitialX18 = int128(s.longWeightInitial) * 1e9;
        r.shortWeightInitialX18 = int128(s.shortWeightInitial) * 1e9;
        r.longWeightMaintenanceX18 = int128(s.longWeightMaintenance) * 1e9;
        r.shortWeightMaintenanceX18 = int128(s.shortWeightMaintenance) * 1e9;
```

**File:** core/contracts/BaseEngine.sol (L162-176)
```text
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
```

**File:** core/contracts/BaseEngine.sol (L235-242)
```text
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
            ERR_BAD_PRODUCT_CONFIG
        );
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
