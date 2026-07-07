### Title
`_getSpreadWeightX18` Uses Long Weight for the Short Leg of a Spread, Corrupting Health and Enabling Premature Liquidation - (File: `core/contracts/libraries/RiskHelper.sol`)

---

### Summary

`RiskHelper._getSpreadWeightX18` computes the spread health bonus using `CoreRisk.longWeight` for the **short** leg of a spread position. However, `CoreRisk.longWeight` is always populated with the long-side weight (because `getCoreRisk` hardcodes `amount = 1` when calling `_getWeightX18`). The short leg's actual short weight — which is strictly greater than 1 — is never consulted. This mirrors the H-01 pattern exactly: a field initialized from one context (long weight, `amount = 1`) is incorrectly applied to a different context (the short leg of a spread).

---

### Finding Description

**Root cause — `getCoreRisk` always stores the long weight:**

In `BaseEngine.getCoreRisk`, the `longWeight` field of `CoreRisk` is always computed by passing the literal `1` (positive) to `_getWeightX18`, regardless of the subaccount's actual position direction:

```solidity
// BaseEngine.sol line 190
RiskHelper._getWeightX18(risk, 1, healthType)
//                             ^ always 1 (positive), never the actual amount
``` [1](#0-0) 

`_getWeightX18` branches on the sign of `amount`: positive → long weight, negative → short weight. Because `1` is always passed, `CoreRisk.longWeight` is always the **long** weight, even when the subaccount holds a short position. [2](#0-1) 

**Misapplication — `_getSpreadWeightX18` uses the wrong weight for the short leg:**

A spread is always composed of two opposite legs (long spot + short perp, or short spot + long perp). `_getSpreadWeightX18` selects which `CoreRisk.longWeight` to use based on `spotCoreRisk.amount`:

```solidity
// RiskHelper.sol lines 66-70
if (spotCoreRisk.amount > 0) {
    spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;  // perp is SHORT here
} else {
    spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;  // spot is SHORT here
}
``` [3](#0-2) 

- When `spotCoreRisk.amount > 0` (long spot, **short** perp): the formula uses `perpCoreRisk.longWeight`, but the perp is short — the correct value is `perpCoreRisk.shortWeight`.
- When `spotCoreRisk.amount <= 0` (short spot, **long** perp): the formula uses `spotCoreRisk.longWeight`, but the spot is short — the correct value is `spotCoreRisk.shortWeight`.

Since `CoreRisk` only carries a single `longWeight` field and `getCoreRisk` always fills it with the long-side weight, the short weight is structurally inaccessible to `_getSpreadWeightX18`. [4](#0-3) 

**Call site — `Clearinghouse.getHealth` drives liquidation decisions:**

The corrupted `spreadWeight` feeds directly into the health delta for every spread-holding subaccount:

```solidity
// Clearinghouse.sol lines 125-135
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
int128 spreadWeight = RiskHelper._getSpreadWeightX18(perpCoreRisk, spotCoreRisk, healthType);
health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
``` [5](#0-4) 

`getHealth` is called by `isUnderMaintenance` in `ClearinghouseLiq`, which gates every liquidation. [6](#0-5) 

---

### Impact Explanation

For a typical product, `longWeightInitial < 1.0` and `shortWeightInitial > 1.0` (enforced by `_addOrUpdateProduct`'s invariant check). [7](#0-6) 

Consider a long-spot / short-perp spread with `perpLongWeight = 0.9` and `perpShortWeight = 1.1`:

| | Buggy (uses `perpLongWeight`) | Correct (uses `perpShortWeight`) |
|---|---|---|
| `spreadWeight` | `1 - (1 - 0.9)/5 = 0.98` | `1 - (1 - 1.1)/5 = 1.02` |
| `existingWeight` | `(0.9 + 0.9)/2 = 0.90` | same |
| `spreadWeight - existingWeight` | `0.08` | `0.12` |

The health bonus is **underestimated by 33%** in this example. For a subaccount whose health is near zero, this systematic underestimation pushes the computed health below zero, causing `isUnderMaintenance` to return `true` and allowing a liquidator to liquidate a position that is actually solvent. The liquidatee suffers an unwarranted liquidation penalty.

---

### Likelihood Explanation

Any subaccount holding a registered spread pair (encoded in `spreads`) with a non-trivial position size is affected on every health check. The trigger requires no special permissions: any liquidator can call `liquidateSubaccount` through the `Endpoint`, which invokes `isUnderMaintenance`. The miscalculation is deterministic and proportional to position size, making it reliably exploitable whenever `longWeight ≠ shortWeight` (the normal case for all non-stablecoin products).

---

### Recommendation

`CoreRisk` must carry both the long and short weights, or `getCoreRisk` must pass the actual `amount` (not the hardcoded `1`) to `_getWeightX18` so the stored weight reflects the real position direction.

**Option A — pass actual `amount` to `_getWeightX18` in `getCoreRisk`:**

```solidity
// BaseEngine.sol
return IProductEngine.CoreRisk(
    amount,
    risk.priceX18,
-   RiskHelper._getWeightX18(risk, 1, healthType)
+   RiskHelper._getWeightX18(risk, amount, healthType)
);
```

**Option B — extend `CoreRisk` to carry both weights:**

```solidity
struct CoreRisk {
    int128 amount;
    int128 price;
    int128 longWeight;
+   int128 shortWeight;
}
```

And update `_getSpreadWeightX18` to use `perpCoreRisk.shortWeight` when the perp is short, and `spotCoreRisk.shortWeight` when the spot is short.

---

### Proof of Concept

1. Admin registers a spread pair: spot product `S` (`longWeightInitial = 0.9e9`, `shortWeightInitial = 1.1e9`) and perp product `P` (same weights).
2. Alice opens: long 10 S-spot + short 10 S-perp (a valid spread).
3. A liquidator calls `Endpoint.submitTransactions` with a `LiquidateSubaccount` transaction targeting Alice.
4. `ClearinghouseLiq.isUnderMaintenance` calls `Clearinghouse.getHealth(alice, MAINTENANCE)`.
5. Inside `getHealth`, `getCoreRisk` returns `perpCoreRisk.longWeight = 0.9` (hardcoded `amount=1`), even though Alice's perp is short.
6. `_getSpreadWeightX18` computes `spreadWeight = 1 - (1 - 0.9)/5 = 0.98` instead of the correct `1 - (1 - 1.1)/5 = 1.02`.
7. The health delta is `10 * price * (0.98 - 0.90) = 10 * price * 0.08` instead of `10 * price * 0.12`.
8. If Alice's base health (without spread bonus) is `-10 * price * 0.09`, the buggy result is `< 0` (liquidatable) while the correct result is `> 0` (solvent).
9. The liquidator successfully liquidates Alice, collecting fees on a position that should have been protected.

### Citations

**File:** core/contracts/BaseEngine.sol (L186-191)
```text
        return
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
```

**File:** core/contracts/BaseEngine.sol (L236-242)
```text
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
            ERR_BAD_PRODUCT_CONFIG
        );
```

**File:** core/contracts/libraries/RiskHelper.sol (L34-55)
```text
    function _getWeightX18(
        Risk memory risk,
        int128 amount,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }

        int128 weight;
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
        } else {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.shortWeightInitialX18
                : risk.shortWeightMaintenanceX18;
        }

        return weight;
    }
```

**File:** core/contracts/libraries/RiskHelper.sol (L66-70)
```text
        if (spotCoreRisk.amount > 0) {
            spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
        } else {
            spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
        }
```

**File:** core/contracts/interfaces/engine/IProductEngine.sol (L30-34)
```text
    struct CoreRisk {
        int128 amount;
        int128 price;
        int128 longWeight;
    }
```

**File:** core/contracts/Clearinghouse.sol (L125-135)
```text
            int128 existingWeight = (spotCoreRisk.longWeight +
                perpCoreRisk.longWeight) / 2;
            int128 spreadWeight = RiskHelper._getSpreadWeightX18(
                perpCoreRisk,
                spotCoreRisk,
                healthType
            );

            health += basisAmount
                .mul(spotCoreRisk.price + perpCoreRisk.price)
                .mul(spreadWeight - existingWeight);
```

**File:** core/contracts/ClearinghouseLiq.sol (L51-58)
```text
    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```
