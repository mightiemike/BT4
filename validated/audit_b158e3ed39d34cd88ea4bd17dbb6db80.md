### Title
Maker receives zero payment due to unguarded rounding-to-zero in TRC10 exchange order matching - (`actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchSingleOrder` computes the settlement amount for a maker order using floor (integer) division via `MarketUtils.multiplyAndDivide`. In two of the three matching branches ("taker < maker" and "taker > maker") the code explicitly checks whether the computed amount rounds down to zero and, if so, refunds the tokens instead of executing a zero-value trade. The third branch — "taker == maker" (exact fill) — has no such guard, so a maker whose remaining order is fully consumed can be paid **zero** buy-token while their entire remaining sell-token balance is consumed and the order is marked `INACTIVE`.

### Finding Description
In `matchSingleOrder`, the taker's desired buy amount is first computed with floor division: [1](#0-0) 
When this rounds to zero, the code protects the taker by returning their sell tokens: [2](#0-1) 

In the "taker > maker" branch, the maker's proceeds are computed with the same kind of floor division, and an explicit zero-check protects the maker: [3](#0-2) 

However, in the "taker == maker" branch (exact match, both orders fully consumed), the maker's proceeds are computed the same way but with **no zero-check**: [4](#0-3) 

`makerBuyTokenQuantityReceive` is derived from `MarketUtils.multiplyAndDivide`, which performs `floorDiv(a*b, c)`: [5](#0-4) 

If `makerSellRemainQuantity * makerBuyQuantity < makerSellQuantity`, this floors to `0`. Since this branch unconditionally sets `makerOrderCapsule.setSellTokenQuantityRemain(0)` and marks the maker order `INACTIVE` regardless of the computed payout, the maker's entire remaining sell-token balance is consumed for a payout of `0`. The subsequent `addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive)` call simply adds zero to the maker's balance — no exception, no revert, no refund.

This is exactly the rounding-class bug from the report: for low-quantity/low "precision" order remainders (analogous to zero-decimal tokens), floor division amplifies rounding loss up to 100% for the affected party, and unlike the sibling branches, this code path lacks the corresponding safeguard.

### Impact Explanation
A maker order that is exactly matched (taker's residual buy-demand equals the maker's remaining sell-quantity) can be fully executed while the maker receives zero proceeds, resulting in a complete, unrecoverable loss of the maker's remaining token balance in that order. This is a direct value-loss/accounting bug reachable by any unprivileged user placing/matching TRC10 market orders via `MarketSellAssetActuator`/`MarketCancelOrderActuator` order book (`MarketSellAssetContract`), with no special privilege required — an attacker (as taker) can deliberately choose sell/buy quantities to trigger the "taker == maker" branch against a target maker order whose price ratio and remaining quantity produce a zero-rounding result, effectively confiscating the maker's tokens.

### Likelihood Explanation
Triggering the bug requires the attacker (as taker) to size their own order such that `takerBuyTokenQuantityRemain` exactly equals the target maker's `sellTokenQuantityRemain`, and for the maker's price ratio (`makerBuyQuantity`/`makerSellQuantity`) combined with that remainder to floor-divide to zero. Both the taker's order size and the choice of maker order to match against are fully attacker-controlled (subject to the market's existing resting orders), making this practically constructible, particularly against maker orders with small remaining quantities or extreme price ratios (which are permitted since `MarketSellAssetContract` only requires `sellTokenQuantity > 0` and `buyTokenQuantity > 0`, with no minimum-value/precision floor other than `getMarketQuantityLimit()`): [6](#0-5) 

### Recommendation
Add the same zero-value guard used in the other two branches to the "taker == maker" branch: if `makerBuyTokenQuantityReceive == 0`, do not zero out and deactivate the maker order — instead return the maker's remaining sell-token balance (as done via `MarketUtils.returnSellTokenRemain`/`returnSellTokenRemain(makerOrderCapsule)`) rather than executing a zero-payout trade, mirroring the "taker > maker" branch's handling.

### Proof of Concept
1. Maker places an order selling `makerSellTokenQuantity` for `makerBuyTokenQuantity`, chosen so the price ratio is extreme (e.g. sell 3 units of asset A for 1 unit of asset B). This order is partially filled by prior trades so that only `makerSellRemainQuantity = 1` remains (`makerBuyQuantity = 1`, `makerSellQuantity = 3`).
2. Taker submits a `MarketSellAssetContract` sized so that `takerBuyTokenQuantityRemain` (computed in `matchSingleOrder` via `MarketUtils.multiplyAndDivide`) equals exactly `1`, matching the maker's remaining sell quantity — this routes execution into the "taker == maker" branch at `MarketSellAssetActuator.java:418-439`.
3. Inside that branch, `makerBuyTokenQuantityReceive = multiplyAndDivide(1, 1, 3, ...) = floorDiv(1*1, 3) = 0`.
4. `makerOrderCapsule.setSellTokenQuantityRemain(0)` executes unconditionally and the maker order is set `INACTIVE`; `addTrxOrToken(makerOrderCapsule, 0)` adds zero to the maker's account.
5. Result: the maker's entire remaining 1 unit of sell-token is consumed by the trade, and the maker receives 0 of the buy-token — a 100% loss on that remainder, with no error thrown and no compensating refund, unlike the analogous zero-check present in the "taker > maker" branch.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L223-230)
```java
    if (sellTokenQuantity <= 0 || buyTokenQuantity <= 0) {
      throw new ContractValidateException("token quantity must greater than zero");
    }

    long quantityLimit = dynamicStore.getMarketQuantityLimit();
    if (sellTokenQuantity > quantityLimit || buyTokenQuantity > quantityLimit) {
      throw new ContractValidateException("token quantity must less than " + quantityLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L402-413)
```java
    long takerBuyTokenQuantityRemain = MarketUtils
        .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
            this.disableJavaLangMath());

    if (takerBuyTokenQuantityRemain == 0) {
      // quantity too small, return sellToken to user
      takerOrderCapsule.setSellTokenQuantityReturn();
      MarketUtils.returnSellTokenRemain(takerOrderCapsule, takerAccountCapsule,
          dynamicStore, assetIssueStore);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);
      return;
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
