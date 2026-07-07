### Title
Single Hardcoded Minimum Liquidation Penalty Floor Applied Uniformly Across All Products Regardless of Volatility - (`File: core/contracts/ClearinghouseStorage.sol`)

---

### Summary

`ClearinghouseStorage.sol` defines two global constant minimum liquidation penalty floors — `MIN_NON_SPREAD_LIQ_PENALTY_X18` (0.5%) and `MIN_SPREAD_LIQ_PENALTY_X18` (0.25%) — that are applied uniformly to every product in `getLiqPriceX18` and `getSpreadLiqPriceX18`. For low-volatility products (e.g., stablecoins) whose per-product maintenance weights are configured close to 1.0, the natural penalty derived from those weights falls below the floor, causing the floor to override it with a disproportionately large penalty. This is the direct analog to M-12: a one-size-fits-all parameter that distorts outcomes differently depending on each product's actual volatility profile.

---

### Finding Description

`getLiqPriceX18` computes the liquidation penalty as:

```solidity
int128 penaltyX18 = (RiskHelper._getWeightX18(
    risk, amount, IProductEngine.HealthType.MAINTENANCE
) - ONE) / 5;
if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
    penaltyX18 = (penaltyX18 < 0)
        ? -MIN_NON_SPREAD_LIQ_PENALTY_X18
        : MIN_NON_SPREAD_LIQ_PENALTY_X18;
}
return (risk.priceX18.mul(ONE + penaltyX18), risk.priceX18);
``` [1](#0-0) 

The same floor logic is applied in `getSpreadLiqPriceX18` using `MIN_SPREAD_LIQ_PENALTY_X18`: [2](#0-1) 

Both constants are hardcoded in `Constants.sol`:

```solidity
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%
int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
``` [3](#0-2) 

The natural penalty is derived from the per-product `RiskStore` maintenance weight: [4](#0-3) 

For a stablecoin product configured with `longWeightMaintenance = 0.999e9` (i.e., 0.999 in 1e9 fixed-point), the natural penalty is:

```
penaltyX18 = (0.999e18 - 1e18) / 5 = -0.0002e18  →  -0.02%
```

Since `|-0.02%| < 0.5%`, the floor overrides it to `-0.5%`. The liquidation price becomes `oraclePrice × 0.995` — a 25× amplification of the warranted discount. For a volatile product with `longWeightMaintenance = 0.9e9`, the natural penalty is `-4%`, which exceeds the floor and is unaffected.

The liquidation fee charged to the liquidator is then computed as:

```solidity
v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
    .mul(LIQUIDATION_FEE_FRACTION)
    .mul(txn.amount);
``` [5](#0-4) 

With `LIQUIDATION_FEE_FRACTION = 50%`, the liquidator effectively acquires the stablecoin at `oraclePrice × (1 − 0.25%)` instead of the warranted `oraclePrice × (1 − 0.01%)`.

---

### Impact Explanation

For any product whose per-product maintenance weight is configured close to 1.0 (natural penalty < 0.5%), the floor forces a disproportionately large liquidation discount:

- **Liquidatee** receives proceeds 0.5% below oracle instead of the warranted ~0.02%, suffering an excess loss of ~0.48% of position value per liquidation event.
- **Liquidator** acquires the asset at an artificially large discount, creating an economic incentive to aggressively liquidate marginally unhealthy stablecoin positions that would otherwise be near-neutral risk.
- **Insurance fund** accrues excess fees from the inflated spread, distorting the protocol's fee accounting relative to actual risk.

The corrupted state delta is the `spotEngine` or `perpEngine` balance update in `_handleLiquidationPayment`: the liquidatee's quote balance is credited with `liquidationPriceX18 × amount` where `liquidationPriceX18` is artificially depressed by the floor, causing a concrete, measurable asset loss per liquidation. [6](#0-5) 

---

### Likelihood Explanation

The trigger requires:
1. A product registered in the protocol with maintenance weights close to 1.0 (stablecoin or near-stable asset). This is a realistic and expected configuration for stablecoin spot products.
2. A subaccount holding that product that falls below maintenance health — a routine occurrence in any active DEX.
3. Any unprivileged liquidator calling `liquidateSubaccount` via the `Endpoint`, which routes through `Clearinghouse.liquidateSubaccount` → `delegatecall` to `ClearinghouseLiq.liquidateSubaccountImpl` → `_handleLiquidationPayment` → `getLiqPriceX18`. [7](#0-6) 

No special privileges, governance capture, or key compromise are required. Any liquidator can trigger this on any eligible subaccount.

---

### Recommendation

Replace the single global floor constants with per-product configurable minimum penalty values stored in the product's `RiskStore` or a separate per-product config mapping. This mirrors the M-12 recommendation: allow each product's risk parameters to encode its own minimum liquidation penalty, so stablecoin products can use a floor of e.g. 0.02% while volatile products use 0.5% or higher.

Alternatively, derive the minimum floor dynamically from the product's maintenance weight spread (e.g., `|maintenanceWeight - 1| / 5` with a protocol-wide absolute minimum of e.g. 0.01%), so the floor scales with the product's configured risk rather than being a fixed global constant.

---

### Proof of Concept

**Setup:** A stablecoin spot product is added with `longWeightMaintenance = 0.999e9` (0.999). A subaccount holds 10,000 units of this stablecoin and falls below maintenance health.

**Step 1 — Natural penalty calculation:**
```
penaltyX18 = (0.999e18 - 1e18) / 5 = -2e14  →  -0.02%
```

**Step 2 — Floor override:**
```
|-2e14| < MIN_NON_SPREAD_LIQ_PENALTY_X18 (5e15)
→ penaltyX18 = -5e15  →  -0.5%
```

**Step 3 — Liquidation price:**
```
liquidationPrice = oraclePrice × (1 - 0.005) = oraclePrice × 0.995
```

**Step 4 — Liquidation fees (50% of spread):**
```
liquidationFees = (oraclePrice - liquidationPrice) × 0.5 × amount
                = oraclePrice × 0.005 × 0.5 × 10000
                = oraclePrice × 25
```

**Step 5 — Liquidatee loss vs. warranted:**
- Warranted liquidation price: `oraclePrice × 0.9998` (0.02% discount)
- Actual liquidation price: `oraclePrice × 0.995` (0.5% discount)
- Excess loss per 10,000 units at $1 oracle: **$48** extracted from liquidatee beyond what their risk profile warrants

A liquidator calling `liquidateSubaccountImpl` with `productId` pointing to this stablecoin product and `amount = 10000e18` will execute this path, with the floor-inflated penalty applied at: [8](#0-7)

### Citations

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

**File:** core/contracts/ClearinghouseStorage.sol (L100-106)
```text
        if (penaltyX18.abs() < MIN_SPREAD_LIQ_PENALTY_X18) {
            if (penaltyX18 < 0) {
                penaltyX18 = -MIN_SPREAD_LIQ_PENALTY_X18;
            } else {
                penaltyX18 = MIN_SPREAD_LIQ_PENALTY_X18;
            }
        }
```

**File:** core/contracts/common/Constants.sol (L56-58)
```text
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%

int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
```

**File:** core/contracts/libraries/RiskHelper.sol (L14-32)
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

    struct Risk {
        int128 longWeightInitialX18;
        int128 shortWeightInitialX18;
        int128 longWeightMaintenanceX18;
        int128 shortWeightMaintenanceX18;
        int128 priceX18;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L507-543)
```text
        } else if (engine == address(spotEngine)) {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );

            spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );

            if (txn.amount < 0) {
                insurance = spotEngine.updateQuoteFromInsurance(
                    txn.liquidatee,
                    insurance
                );
            }
```

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```
