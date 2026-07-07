### Title
Uninitialized Fee Rates for Newly Added Products When Non-Default Tier Mask Is Set — (`core/contracts/OffchainExchange.sol`)

### Summary

`OffchainExchange.getTierFeeRateX18()` uses `nonDefaultFeeTierMask` to decide whether to return stored `feeRates[tier][productId]` or the hardcoded default (0 maker / 2 bps taker). Once any tier's bit is set in `nonDefaultFeeTierMask` via `updateTierFeeRates`, all subsequent calls for that tier return the stored mapping value — which is zero for any product added after the tier was configured. This means users on that tier pay zero taker fees on newly listed products until the admin explicitly re-runs `updateTierFeeRates` for the new product.

---

### Finding Description

`getTierFeeRateX18` in `OffchainExchange.sol` has the following logic:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];   // returns 0,0 for uninitialized product
    }
    return FeeRates({
        makerRateX18: 0,
        takerRateX18: 200_000_000_000_000   // 2 bps default
    });
}
``` [1](#0-0) 

`updateTierFeeRates` iterates only over products that exist at call time and then sets the tier's bit in `nonDefaultFeeTierMask`:

```solidity
for (uint32 i = 0; i < spotProductIds.length; i++) { ... }
for (uint32 i = 0; i < perpProductIds.length; i++) { ... }
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
``` [2](#0-1) 

After this call, `nonDefaultFeeTierMask` permanently marks the tier as "non-default." Any product added afterward via `SpotEngine.addOrUpdateProduct` or `PerpEngine.addOrUpdateProduct` is never written into `feeRates[tier][newProductId]`, so that mapping slot remains `FeeRates({makerRateX18: 0, takerRateX18: 0})`. [3](#0-2) [4](#0-3) 

In `applyFee`, a zero `feeRate` means `keepRateX18 = ONE`, so `fee = 0`:

```solidity
int128 keepRateX18 = ONE - feeInfo.feeRate;
int128 newMeteredQuote = (meteredQuote > 0)
    ? meteredQuote.mul(keepRateX18)
    : meteredQuote.div(keepRateX18);
orderInfo.fee = meteredQuote - newMeteredQuote;   // = 0 when feeRate = 0
``` [5](#0-4) 

Because every user's default fee tier is `0` (stored as `feeTiers[user] = 0` when never set), and tier 0 is the most likely tier to be configured via `updateTierFeeRates`, this desynchronization affects **all users** on the default tier, not just privileged ones. [6](#0-5) 

---

### Impact Explanation

Protocol fee revenue is zeroed out for every trade on a newly listed product until the admin re-runs `updateTierFeeRates` for that product. For tier 0 (the universal default), this affects every trader. The broken invariant is: *once a tier is marked non-default, all products must have explicit fee rates stored for that tier*. The contract does not enforce this invariant when products are added.

---

### Likelihood Explanation

The trigger sequence is operationally normal: (1) admin calls `updateTierFeeRates` with `productId == QUOTE_PRODUCT_ID` to apply a global fee schedule, (2) admin later lists a new spot or perp product. Step 1 permanently sets the tier bit; step 2 does not touch `feeRates`. The gap between listing a product and setting its per-tier fee rates is a realistic window. The sequencer controls order matching and could delay matching on the new product, but the contract itself provides no enforcement, and sequencer operators may not be aware of this invariant.

---

### Recommendation

In `updateTierFeeRates`, when `productId == QUOTE_PRODUCT_ID`, also initialize `feeRates[tier][newProductId]` for any product added in the future — or, more robustly, modify `getTierFeeRateX18` to fall back to the hardcoded default when the stored rate is zero:

```diff
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
+       FeeRates memory stored = feeRates[tier][productId];
+       if (stored.takerRateX18 != 0 || stored.makerRateX18 != 0) {
+           return stored;
+       }
    }
    return FeeRates({
        makerRateX18: 0,
        takerRateX18: 200_000_000_000_000
    });
}
```

Alternatively, `addOrUpdateProduct` in both engines should call `updateTierFeeRates` for all configured tiers, or the admin tooling must enforce this as an atomic operation.

---

### Proof of Concept

1. Admin calls `updateTierFeeRates({tier: 0, productId: QUOTE_PRODUCT_ID, makerRateX18: 0, takerRateX18: 2e14})`. This iterates all existing products, sets their rates, and sets `nonDefaultFeeTierMask |= 1`.
2. Admin calls `SpotEngine.addOrUpdateProduct(newProductId, ...)`. `feeRates[0][newProductId]` is never written; it remains `FeeRates({0, 0})`.
3. Sequencer submits a `MatchOrders` transaction for `newProductId` with a taker order from Alice (tier 0).
4. `getUserFeeRateWithBuilder` → `getTierFeeRateX18(0, newProductId)` → `nonDefaultFeeTierMask & 1 != 0` → returns `feeRates[0][newProductId]` = `{0, 0}`.
5. `applyFee`: `feeRate = 0`, `keepRateX18 = ONE`, `fee = 0`. Alice pays zero taker fees.
6. `marketInfo[newProductId].collectedFees` accumulates nothing; protocol receives no revenue from trades on the new product.

### Citations

**File:** core/contracts/OffchainExchange.sol (L496-506)
```text
        }

        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
        if (feeTier < builder.defaultFeeTier) {
            feeTier = builder.defaultFeeTier;
        }
        FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
        int128 feeRate = taker
            ? userFeeRates.takerRateX18
            : userFeeRates.makerRateX18;
        return FeeInfo(feeRate, builderId, builderFeeRate);
```

**File:** core/contracts/OffchainExchange.sol (L556-565)
```text
        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
        orderInfo.builderFee = matchQuote.abs().mul(feeInfo.builderFeeRate);
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L933-946)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
    }
```

**File:** core/contracts/OffchainExchange.sol (L962-991)
```text
    function updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
        external
        onlyEndpoint
    {
        if (txn.productId == QUOTE_PRODUCT_ID) {
            uint32[] memory spotProductIds = spotEngine.getProductIds();
            uint32[] memory perpProductIds = perpEngine.getProductIds();
            for (uint32 i = 0; i < spotProductIds.length; i++) {
                if (spotProductIds[i] == QUOTE_PRODUCT_ID) {
                    continue;
                }
                feeRates[txn.tier][spotProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
            for (uint32 i = 0; i < perpProductIds.length; i++) {
                feeRates[txn.tier][perpProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
    }
```

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```

**File:** core/contracts/PerpEngine.sol (L33-57)
```text
    function addOrUpdateProduct(
        uint32 productId,
        int128 sizeIncrement,
        int128 minSize,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            QUOTE_PRODUCT_ID,
            sizeIncrement,
            minSize,
            riskStore
        );

        if (isNewProduct) {
            _setState(
                productId,
                State({
                    cumulativeFundingLongX18: 0,
                    cumulativeFundingShortX18: 0,
                    availableSettle: 0,
                    openInterest: 0
                })
            );
        }
```
