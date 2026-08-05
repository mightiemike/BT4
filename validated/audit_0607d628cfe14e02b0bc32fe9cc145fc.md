### Title
Dust maker orders can be fully consumed for zero payment due to missing zero-check in exact-match branch of `MarketSellAssetActuator.matchSingleOrder` - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java)

### Summary
`MarketSellAssetActuator` implements java-tron's on-chain order-book market for TRC10 tokens/TRX. Like the reported CLOB bug, a partial match can leave a maker order with a "dust" remaining sell quantity, with no minimum-amount check. Unlike the CLOB case (which reverts with `ZeroCostTrade`), java-tron's exact-match branch has **no zero-check at all**, so a dust maker order can be fully consumed while the maker receives zero tokens in return, due to integer division rounding.

### Finding Description
When a taker order partially matches a maker order (taker's buy amount is smaller than the maker's remaining sell amount), the maker's remaining amount is decremented with no minimum-size floor: [1](#0-0) 

This can leave `makerOrderCapsule.getSellTokenQuantityRemain()` at a tiny "dust" value while the order stays `ACTIVE` in the book (it is only removed when `getSellTokenQuantityRemain() == 0`, checked in `matchOrder`): [2](#0-1) 

When a later taker order matches this dust maker order exactly (`takerBuyTokenQuantityRemain == makerOrderCapsule.getSellTokenQuantityRemain()`), the "taker == maker" branch computes `makerBuyTokenQuantityReceive` via integer division and **never checks if the result is zero**, unlike the sibling "taker > maker" branch which explicitly handles this case: [3](#0-2) 

Compare this to the "taker > maker" branch, which correctly detects and handles a zero result by returning the dust back to the maker instead of consuming the order for free: [4](#0-3) 

The rounding is performed by `MarketUtils.multiplyAndDivide`, which uses floor division and can legitimately return `0` for small `makerSellRemainQuantity` values combined with a `makerBuyQuantity/makerSellQuantity` ratio below 1 (i.e., selling many tokens per unit of the buy token): [5](#0-4) 

In the exact-match branch, when `makerBuyTokenQuantityReceive == 0`:
- `makerOrderCapsule.setSellTokenQuantityRemain(0)` fully consumes/deactivates the maker's dust order.
- `addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive)` credits the maker with `0` tokens.
- `addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, ...)` still credits the taker with the full `takerBuyTokenQuantityReceive` (the maker's dust sell tokens), fully satisfying the taker's order. [6](#0-5) 

The root cause mirrors the external report exactly: a maker order is allowed to shrink to a dust amount with no minimum-size enforcement (`CLOB.sol` L807-L849 analog is L440-452 here), and that dust amount is later consumed by a match whose settlement math rounds to zero — but where the CLOB reverts (`ZeroCostTrade`), java-tron's exact-match branch silently proceeds, transferring the maker's assets to the taker for free.

### Impact Explanation
This is a direct fund-loss/accounting-integrity bug: an unprivileged taker can end up receiving a maker's remaining sell tokens without paying anything, because the settlement code does not verify the computed payment is non-zero before finalizing the exact-match branch. This differs from (and is more severe than) the original DoS report because it results in silent value transfer/theft rather than a mere transaction revert, all triggerable by ordinary `MarketSellAssetContract` transactions from any account.

### Likelihood Explanation
Requires (1) an initial partial fill that leaves a maker order at a small remaining quantity (achievable by any user simply submitting a sell order sized to leave dust after matching), and (2) a subsequent taker order whose buy amount exactly equals that dust remaining amount and whose price ratio (`makerBuyQuantity`/`makerSellQuantity` < 1) causes floor-division rounding to zero. Both conditions are attacker-controllable since token quantities and prices are fully within the caller's control when constructing `MarketSellAssetContract` orders, making this practically reproducible, not merely theoretical.

### Recommendation
Add an explicit zero-check to the exact-match ("taker == maker") branch in `matchSingleOrder`, mirroring the handling already present in the "taker > maker" branch: if `makerBuyTokenQuantityReceive == 0`, return the maker's remaining sell tokens instead of transferring them for free. Additionally, consider enforcing a minimum remaining-order-size floor after each partial match (as recommended in the original report) so dust orders cannot persist in the book at all.

### Proof of Concept
1. Attacker A creates a maker sell order: sell 200 units of Token X for 100 units of Token Y (`makerSellQuantity=200`, `makerBuyQuantity=100`), giving a sell:buy ratio of 2:1.
2. A taker order partially matches this order such that the maker's remaining sell quantity is reduced to a small dust value, e.g. `makerSellRemainQuantity = 1` (via the "taker < maker" branch at `MarketSellAssetActuator.java` L440-452, which applies no minimum-size check).
3. Attacker B (or the same attacker via a second account) submits a new sell order whose computed `takerBuyTokenQuantityRemain` equals exactly `1` (the maker's dust remain), landing in the "taker == maker" branch (L418-439).
4. `makerBuyTokenQuantityReceive = multiplyAndDivide(1, 100, 200, …) = floor(100/200) = 0`.
5. The code proceeds to set `makerOrderCapsule.setSellTokenQuantityRemain(0)` (fully closing A's order) and credits B with `takerBuyTokenQuantityReceive = 1` unit of Token X, while A receives `0` units of Token Y — A's last unit of Token X is transferred to B for free.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L349-354)
```java
        // remove order
        if (makerOrderCapsule.getSellTokenQuantityRemain() == 0) {
          // remove from market order list
          orderIdListCapsule.removeOrder(makerOrderCapsule, orderStore,
              pairPriceKey, pairPriceToOrderStore);
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L418-439)
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

      long takerSellTokenLeft =
          takerOrderCapsule.getSellTokenQuantityRemain() - makerBuyTokenQuantityReceive;
      takerOrderCapsule.setSellTokenQuantityRemain(takerSellTokenLeft);
      makerOrderCapsule.setSellTokenQuantityRemain(0);

      if (takerSellTokenLeft == 0) {
        MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);
      }
      MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L440-452)
```java
    } else if (takerBuyTokenQuantityRemain < makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker < maker
      // if the quantity of taker want to buy is smaller than the remain of maker want to sell,
      // consume the order of the taker

      takerBuyTokenQuantityReceive = takerBuyTokenQuantityRemain;
      makerBuyTokenQuantityReceive = takerOrderCapsule.getSellTokenQuantityRemain();

      takerOrderCapsule.setSellTokenQuantityRemain(0);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);

      makerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
          makerOrderCapsule.getSellTokenQuantityRemain(), takerBuyTokenQuantityRemain));
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L461-477)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L485-499)
```java
    // save makerOrderCapsule
    orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule);

    // add token into account
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);

    MarketOrderDetail orderDetail = MarketOrderDetail.newBuilder()
        .setMakerOrderId(makerOrderCapsule.getID())
        .setTakerOrderId(takerOrderCapsule.getID())
        .setFillSellQuantity(makerBuyTokenQuantityReceive)
        .setFillBuyQuantity(takerBuyTokenQuantityReceive)
        .build();
    ret.addOrderDetails(orderDetail);
  }
```

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
