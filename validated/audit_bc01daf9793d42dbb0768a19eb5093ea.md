### Title
Spread Health Adjustment Uses `longWeight` for Short Perp Side, Inflating Subaccount Health — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`getHealth` in `Clearinghouse.sol` computes a spread credit by subtracting an `existingWeight` from a `spreadWeight`. The `existingWeight` is derived from `getCoreRisk`, which hardcodes a positive amount (`1`) when calling `_getWeightX18`, so it always returns `longWeight` for both legs. For a canonical spread (long spot + short perp), the perp leg was already counted in `getHealthContribution` using `shortWeight` (the penalizing weight, > 1), but `existingWeight` uses `longWeight` (< 1) for that leg. This makes `(spreadWeight - existingWeight)` larger than it should be, inflating the spread health credit and overstating the subaccount's health.

---

### Finding Description

`getHealth` in `Clearinghouse.sol` accumulates health from both engines and then applies a spread adjustment for registered spread pairs:

```
health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
``` [1](#0-0) 

`existingWeight` is computed as:

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
``` [2](#0-1) 

Both `spotCoreRisk.longWeight` and `perpCoreRisk.longWeight` come from `getCoreRisk` in `BaseEngine.sol`, which always passes the literal `1` as the `amount` argument to `_getWeightX18`:

```solidity
RiskHelper._getWeightX18(risk, 1, healthType)
``` [3](#0-2) 

Because `_getWeightX18` returns `longWeight` when `amount >= 0`, `getCoreRisk` always returns `longWeight` regardless of the actual sign of the position:

```solidity
if (amount >= 0) {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.longWeightInitialX18
        : risk.longWeightMaintenanceX18;
}
``` [4](#0-3) 

However, `_calculateProductHealth` — which drives `getHealthContribution` — passes the **actual** balance amount to `_getWeightX18`:

```solidity
int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
health += amount.mul(weight).mul(risk.priceX18);
``` [5](#0-4) 

For a spread position (long spot, short perp), the perp leg's health contribution was already counted using `shortWeight` (since `perpAmount < 0`). The `shortWeightMaintenance` is enforced to be `>= 10^9` (i.e., ≥ 1.0), while `longWeight < 1.0`:

```solidity
riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance &&
riskStore.shortWeightMaintenance >= 10**9,
``` [6](#0-5) 

So `existingWeight = (spotLongWeight + perpLongWeight) / 2` is materially lower than the true average weight already applied `(spotLongWeight + perpShortWeight) / 2`. This makes `(spreadWeight - existingWeight)` larger than it should be, producing an inflated positive health adjustment.

---

### Impact Explanation

The inflated spread credit directly overstates `getHealth`, which is the gating check for:

1. **`withdrawCollateral`** — a user can withdraw more collateral than their true health supports, draining assets from the protocol.
2. **`liquidateSubaccount`** — a subaccount that should be liquidatable passes the health check and escapes liquidation, leaving bad debt. [7](#0-6) 

The magnitude of the overstatement scales with `basisAmount * (spotPrice + perpPrice)` and the gap `(perpShortWeight - perpLongWeight)`. For typical risk parameters (e.g., `longWeight = 0.9`, `shortWeight = 1.1`), the spread adjustment flips from a small penalty to a meaningful bonus, allowing excess leverage or preventing valid liquidations.

---

### Likelihood Explanation

Any unprivileged user who holds a registered spread pair (long spot + short perp, or short spot + long perp) triggers this path. Opening such a position is a normal, supported trading action via `OffchainExchange`. No special permissions, governance access, or leaked keys are required. The spread pairs are registered at deployment via `setSpreads`, so the affected products are known and active.

---

### Recommendation

`getCoreRisk` should pass the **actual** balance amount (not the hardcoded `1`) to `_getWeightX18`, so that `existingWeight` reflects the weight that was actually applied in `getHealthContribution`:

```solidity
// In BaseEngine.getCoreRisk:
return IProductEngine.CoreRisk(
    amount,
    risk.priceX18,
-   RiskHelper._getWeightX18(risk, 1, healthType)   // always longWeight
+   RiskHelper._getWeightX18(risk, amount, healthType) // actual weight
);
```

This ensures `existingWeight = (spotActualWeight + perpActualWeight) / 2` correctly represents what was already counted, so the spread delta `(spreadWeight - existingWeight)` is accurate.

---

### Proof of Concept

**Setup:** Spread pair registered: spot product `S` (longWeightInitial = 0.9e18, shortWeightInitial = 1.1e18), perp product `P` (same weights). Price = 1.0 for both. `spreadWeight` ≈ 0.99.

**Position:** User holds `+100` spot, `-100` perp (a perfect hedge).

**Health already counted by `getHealthContribution`:**
- Spot: `100 * 0.9 * 1.0 = +90`
- Perp: `-100 * 1.1 * 1.0 = -110`
- Subtotal: `-20`

**Spread adjustment (buggy):**
- `basisAmount = min(100, 100) = 100`
- `existingWeight = (0.9 + 0.9) / 2 = 0.9` ← uses `longWeight` for perp
- `spreadWeight ≈ 0.99`
- Adjustment: `100 * (1.0 + 1.0) * (0.99 - 0.9) = 100 * 2 * 0.09 = +18`
- **Total health: `-20 + 18 = -2`** (nearly zero, user appears almost healthy)

**Correct spread adjustment:**
- `existingWeight = (0.9 + 1.1) / 2 = 1.0` ← uses actual `shortWeight` for perp
- Adjustment: `100 * 2 * (0.99 - 1.0) = -2`
- **Correct total health: `-20 + (-2) = -22`** (clearly unhealthy, should be liquidatable)

The user exploits the inflated health to call `withdrawCollateral` and extract collateral that the protocol should not release, or to avoid a valid liquidation. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Clearinghouse.sol (L86-138)
```text
        uint256 _spreads = spreads;
        while (_spreads != 0) {
            uint32 _spotId = uint32(_spreads & 0xFF);
            _spreads >>= 8;
            uint32 _perpId = uint32(_spreads & 0xFF);
            _spreads >>= 8;

            IProductEngine.CoreRisk memory perpCoreRisk = perpEngine
                .getCoreRisk(subaccount, _perpId, healthType);

            if (perpCoreRisk.amount == 0) {
                continue;
            }

            IProductEngine.CoreRisk memory spotCoreRisk = spotEngine
                .getCoreRisk(subaccount, _spotId, healthType);

            if (
                (spotCoreRisk.amount == 0) ||
                ((spotCoreRisk.amount > 0) == (perpCoreRisk.amount > 0))
            ) {
                continue;
            }

            int128 basisAmount;
            if (spotCoreRisk.amount > 0) {
                basisAmount = MathHelper.min(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            } else {
                basisAmount = -MathHelper.max(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            }

            // spreads have 5x higher leverage than the underlying products.
            // but it's capped at 100x leverage at most.
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
            emit PriceQuery(_spotId);
            emit PriceQuery(_perpId);
        }
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/BaseEngine.sol (L167-174)
```text
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
```

**File:** core/contracts/BaseEngine.sol (L179-192)
```text
    function getCoreRisk(
        bytes32 subaccount,
        uint32 productId,
        IProductEngine.HealthType healthType
    ) external returns (IProductEngine.CoreRisk memory) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, ) = _getBalance(productId, subaccount);
        return
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
    }
```

**File:** core/contracts/BaseEngine.sol (L238-240)
```text
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
```

**File:** core/contracts/libraries/RiskHelper.sol (L44-47)
```text
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
```
