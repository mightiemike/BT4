### Title
Static Position-Size-Independent Weight in Health Calculation Enables Collateral Overvaluation and Bad Debt on Large Position Liquidations — (File: `core/contracts/BaseEngine.sol`)

---

### Summary

`_calculateProductHealth` in `BaseEngine.sol` computes every subaccount's health contribution as `amount × weight × price`, where `weight` is a static, admin-set parameter that is completely independent of position size. For large positions, this systematically overestimates realizable collateral value because liquidating a large position causes market impact that drives the actual exit price well below the oracle price. The protocol therefore permits users to borrow more than is safely collateralized, and when those large positions are eventually liquidated, the insurance fund must absorb the shortfall — creating bad debt.

---

### Finding Description

In `BaseEngine._calculateProductHealth`, the health contribution of any position is:

```solidity
health += amount.mul(weight).mul(risk.priceX18);   // BaseEngine.sol L174
```

`weight` is resolved by `RiskHelper._getWeightX18`, which selects one of four static stored values (`longWeightInitialX18`, `longWeightMaintenanceX18`, `shortWeightInitialX18`, `shortWeightMaintenanceX18`) based solely on the **sign** of `amount` and the `healthType` enum — never on the **magnitude** of `amount`:

```solidity
// RiskHelper.sol L44-54
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
```

These weights are stored as `int32` fields in `RiskHelper.RiskStore` and written once by the admin via `updateRisk` or at product registration. They are never adjusted at runtime based on how large a position has grown.

The same static-weight pattern propagates into the liquidation price calculation in `ClearinghouseStorage.getLiqPriceX18`:

```solidity
int128 penaltyX18 = (RiskHelper._getWeightX18(
    risk, amount, IProductEngine.HealthType.MAINTENANCE
) - ONE) / 5;
return (risk.priceX18.mul(ONE + penaltyX18), risk.priceX18);
```

The penalty is derived from the same static weight, so the liquidation price is also independent of position size.

**Consequence**: a subaccount holding 1 unit of an asset and a subaccount holding 1,000,000 units of the same asset receive identical per-unit health credit. The protocol implicitly assumes that any quantity can be liquidated at `weight × oraclePrice` with no market impact, which is false for large positions in any market with finite depth.

---

### Impact Explanation

A user who accumulates a large long position is permitted to borrow USDC up to the health limit computed as `large_amount × longWeightMaintenance × oraclePrice`. Because this limit is linearly proportional to position size with no size-dependent haircut, the user can borrow more than the market can actually absorb at liquidation time.

When the position becomes unhealthy and is liquidated via `ClearinghouseLiq.liquidateSubaccountImpl` → `_handleLiquidationPayment`, the liquidation payment is `liquidationPrice × amount` where `liquidationPrice` is still derived from the same static oracle price and static penalty. The liquidator takes on the full position at that price. If the position is large enough that the market cannot absorb it at the oracle price, the liquidator suffers a loss, reducing the incentive to liquidate. If the liquidatee's quote balance remains negative after liquidation, the insurance fund (`insurance` in `ClearinghouseStorage`) must cover the deficit. If the deficit exceeds the insurance fund, the loss is socialized via `perpEngine.socializeSubaccount` / `spotEngine.socializeSubaccount`, constituting protocol-level bad debt.

---

### Likelihood Explanation

The protocol imposes no position-size cap or concentration limit on any product. Any trader — including a deliberate attacker — can accumulate an arbitrarily large long position through the normal `Endpoint` → `OffchainExchange` → `SpotEngine`/`PerpEngine` flow. The entry path is fully unprivileged. In volatile or low-liquidity market conditions (which are precisely the conditions that trigger liquidations), the gap between oracle price and realizable exit price for a large position can be substantial. The scenario is realistic for any product with limited on-chain or off-chain liquidity depth.

---

### Recommendation

Introduce a position-size-dependent discount into `_getWeightX18` (or a wrapper called from `_calculateProductHealth`) so that the effective weight decreases as `|amount|` grows beyond configurable thresholds. For example:

```solidity
// pseudo-code
if (amount > LARGE_POSITION_THRESHOLD_1) {
    weight = weight.mul(SLIPPAGE_DISCOUNT_1);
} else if (amount > LARGE_POSITION_THRESHOLD_2) {
    weight = weight.mul(SLIPPAGE_DISCOUNT_2);
}
```

Apply the same size-dependent discount in `getLiqPriceX18` so that the liquidation price also reflects expected market impact for large positions. Thresholds and discount factors should be set per-product based on observed market depth.

---

### Proof of Concept

**Setup** (all values in 18-decimal fixed-point, simplified):

| Step | Action | State |
|---|---|---|
| 1 | User deposits 1,000 USDC | quote balance = +1,000 |
| 2 | User opens long 500,000 units of asset X at oracle price = 1.0 | spot balance = +500,000 |
| 3 | Health (maintenance) = 500,000 × 0.9 × 1.0 + 1,000 = **451,000** | |
| 4 | User borrows 450,000 USDC | quote balance = 1,000 − 450,000 = **−449,000** |
| 5 | Health = 500,000 × 0.9 × 1.0 − 449,000 = **1,000** (just above maintenance) | |
| 6 | Oracle price drops to 0.998; health = 500,000 × 0.9 × 0.998 − 449,000 = **−100** → liquidatable | |
| 7 | Liquidation price = 0.998 × (1 − 0.005) ≈ **0.993** (static 0.5% penalty) | |
| 8 | Liquidation payment = 0.993 × 500,000 = **496,500 USDC** credited to liquidatee | |
| 9 | Liquidatee quote: −449,000 + 496,500 = **+47,500** — appears solvent | |
| 10 | Liquidator now holds 500,000 units; actual market exit price = **0.85** (market impact of selling 500,000 units) | |
| 11 | Liquidator loss = (0.993 − 0.85) × 500,000 = **71,500 USDC** | |
| 12 | Liquidators refuse to liquidate at a loss → position sits undercollateralized → bad debt accumulates | |

The root cause at every step is that `_calculateProductHealth` at line 174 of `BaseEngine.sol` and `getLiqPriceX18` at lines 47–59 of `ClearinghouseStorage.sol` both use a weight/penalty that is invariant to position size, allowing the protocol to treat 500,000 units identically to 1 unit on a per-unit basis.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** core/contracts/ClearinghouseStorage.sol (L41-60)
```text
    function getLiqPriceX18(uint32 productId, int128 amount)
        internal
        returns (int128, int128)
    {
        RiskHelper.Risk memory risk = IProductEngine(productToEngine[productId])
            .getRisk(productId);
        int128 penaltyX18 = (RiskHelper._getWeightX18(
            risk,
            amount,
            IProductEngine.HealthType.MAINTENANCE
        ) - ONE) / 5;
        if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
            if (penaltyX18 < 0) {
                penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18;
            } else {
                penaltyX18 = MIN_NON_SPREAD_LIQ_PENALTY_X18;
            }
        }
        return (risk.priceX18.mul(ONE + penaltyX18), risk.priceX18);
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L544-570)
```text
        } else {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );
            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
            perpEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount,
                v.liquidationPayment
            );
            perpEngine.updateBalance(
                txn.productId,
                txn.sender,
                txn.amount,
                -v.liquidationPayment
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationFees
            );
        }
```
