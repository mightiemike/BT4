## Analog Found

### Title
Maker order settlement uses floor-division for buy-token payout, causing makers to receive less than the exact matched value - ([File: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java])

### Summary
The TRC10 on-chain order-book market (`MarketSellAssetActuator`) settles maker/taker trades using `MarketUtils.multiplyAndDivide`, which always performs a floor (round-down) integer division regardless of who the rounding favors. When a resting "maker" order is fully consumed, the amount of buy-token it receives is computed with this floor division, so the maker is credited strictly less than the exact proportional value of the tokens it sold — the same rounding-direction defect as the Sherlock Bond report, where `_currentMarketPrice` rounded down instead of up and caused makers to sell below the intended price.

### Finding Description
`MarketUtils.multiplyAndDivide` computes `a*b/c` using `floorDiv`, with no rounding-up variant: [1](#0-0) 

In `MarketSellAssetActuator.matchSingleOrder`, whenever a maker's remaining sell quantity is fully consumed (the "taker == maker" and "taker > maker" branches), the buy-token amount the maker receives is derived from this same floor division: [2](#0-1) [3](#0-2) 

In both branches, `makerOrderCapsule.setSellTokenQuantityRemain(0)` fully depletes the maker's sell-side balance, but `makerBuyTokenQuantityReceive = multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity)` is rounded down. Whenever `makerSellRemainQuantity * makerBuyQuantity` is not an exact multiple of `makerSellQuantity` (which is the common case for any resting order that was previously partially filled, since `makerSellRemainQuantity` is then an arbitrary residual rather than the original `makerSellQuantity`), the maker is paid strictly less buy-token than the fair ratio dictates. The lost fractional value is not returned to the maker nor credited to the taker — it simply vanishes from the settlement, exactly mirroring the whitepaper violation in the Sherlock report where the integer price implementation must round up to avoid the maker "selling tokens at a lower price than expected."

This differs from the `ExchangeProcessor`/`SafeExchangeProcessor` Bancor-style AMM (`chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`), where truncation toward zero on both legs deliberately favors the pool/liquidity providers (a defensive design choice consistent with standard AMM rounding practice). In the order-book market, however, there is no such protective design — the rounding indiscriminately shorts the maker on every non-exact match against a partially-filled resting order.

### Impact Explanation
Any unprivileged user acting as a maker (placing a `MarketSellAssetContract` order into the order book) who has their order partially filled multiple times will lose a fraction of the expected buy-token payout on each subsequent full-consumption fill, because the remaining sell quantity after partial fills is rarely an exact multiple relationship with the original `sellQuantity`/`buyQuantity` ratio. This is a genuine, deterministic accounting shortfall against ordinary market participants (accounting/underpriced-settlement impact), reachable by any account using the public `MarketSellAssetContract` flow — no privileged role required.

### Likelihood Explanation
High likelihood of occurrence: any maker order that receives more than one partial fill from different takers will almost certainly hit a non-exact division at the final consuming match, since `makerSellRemainQuantity` after partial fills is not guaranteed to preserve the exact `sellQuantity:buyQuantity` ratio. The test suite itself acknowledges this behavior in comments describing "Accuracy problem" scenarios for partial matches: [4](#0-3) 

### Recommendation
When the maker's remaining sell quantity is being fully consumed (i.e., the maker will receive no further fills for this order), the buy-token amount owed to the maker should be rounded up (ceiling division) rather than floored, so the maker never receives less than the exact proportional value for the tokens it sold. A `multiplyAndDivideRoundUp` variant of `MarketUtils.multiplyAndDivide` should be used specifically in the branches that finalize/close out the maker's order (lines 426-428 and 461-463 of `MarketSellAssetActuator.java`), analogous to switching `_currentMarketPrice` from `mulDiv` to `mulDivUp` in the referenced Bond Protocol fix.

### Proof of Concept
1. Maker places order: sell 101 of token A for 200 of token B (`makerSellQuantity=101`, `makerBuyQuantity=200`).
2. A first taker partially fills the order, reducing `makerSellRemainQuantity` to, say, 51 (an amount not evenly divisible against the 101:200 ratio).
3. A second taker fully consumes the remaining 51 units via the "taker == maker" or "taker > maker" branch. The buy amount computed is:
   `multiplyAndDivide(51, 200, 101) = floor(10200/101) = floor(100.99) = 100`
   whereas the exact fair value is `100.99...`, so the maker is credited only 100 instead of the fractional-rounded-up value it is owed, and `makerOrderCapsule.setSellTokenQuantityRemain(0)` closes the order — the ~0.99 unit shortfall is permanently lost to the maker with no compensating credit anywhere in the flow, matching lines 461-463 of `MarketSellAssetActuator.java`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L264-277)
```java
  public static long multiplyAndDivide(long a, long b, long c, boolean disableMath) {
    try {
      long tmp = multiplyExact(a, b, disableMath);
      return floorDiv(tmp, c, disableMath);
    } catch (ArithmeticException ex) {
      // do nothing here, because we will use BigInteger to compute again
    }

    BigInteger aBig = BigInteger.valueOf(a);
    BigInteger bBig = BigInteger.valueOf(b);
    BigInteger cBig = BigInteger.valueOf(c);

    return aBig.multiply(bBig).divide(cBig).longValue();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L418-429)
```java
    if (takerBuyTokenQuantityRemain == makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker == maker

      // makerSellTokenQuantityRemain_A/makerBuyTokenQuantityCurrent_TRX =
      //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX
      // => makerBuyTokenQuantityCurrent_TRX = makerSellTokenQuantityRemain_A *
      //   makerBuyTokenQuantity_TRX / makerSellTokenQuantity_A

      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-482)
```java
    } else {
      // taker > maker
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();

      // if the quantity of taker want to buy is bigger than the remain of maker want to sell,
      // consume the order of maker
      // makerSellTokenQuantityRemain_A/makerBuyTokenQuantityCurrent_TRX =
      //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());

      MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
      if (makerBuyTokenQuantityReceive == 0) {
        // the quantity is too small, return the remain of sellToken to maker
        // it would not happen here
        // for the maker, when sellQuantity < buyQuantity, it will get at least one buyToken
        // even when sellRemain = 1.
        // so if sellQuantity=200，buyQuantity=100, when sellRemain=1, it needs to be satisfied
        // the following conditions:
        // makerOrderCapsule.getSellTokenQuantityRemain() - takerBuyTokenQuantityRemain = 1
        // 200 - 200/100 * X = 1 ===> X = 199/2，and this comports with the fact that X is integer.
        makerOrderCapsule.setSellTokenQuantityReturn();
        returnSellTokenRemain(makerOrderCapsule);
        return;
      } else {
        makerOrderCapsule.setSellTokenQuantityRemain(0);
        takerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
            takerOrderCapsule.getSellTokenQuantityRemain(), makerBuyTokenQuantityReceive));
      }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L617-623)
```java
  //    all match with 2 existing same price buy orders and complete all 3 orders
  //    part match with 2 existing buy orders and complete the makers,
  //        left enough
  //        left not enough and return left（Accuracy problem）
  //    part match with 2 existing buy orders and complete the taker,
  //        left enough
  //        left not enough and return left（Accuracy problem）（not exist)
```
