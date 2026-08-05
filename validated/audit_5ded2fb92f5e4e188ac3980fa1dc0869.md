### Title
Market order matching underpays sellers due to floor-rounding in price quantity calculation - (File: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java)

### Summary
`MarketUtils.multiplyAndDivide`, used by `MarketSellAssetActuator.matchSingleOrder` to compute the exact settlement quantity a maker or taker receives when matching TRC10 orders in TRON's on-chain order-book market, always rounds the result down (`floorDiv`), never up. This mirrors the Sherlock M-10 bond-protocol bug where the debt-decay increment was rounded down instead of up, causing the price to decay (and thus the counterparty's proceeds) to be systematically lower than the mathematically fair value.

### Finding Description
`multiplyAndDivide` performs `floorDiv(a * b, c)` with no compensating rounding-up path: [1](#0-0) 

This helper is invoked three times in `MarketSellAssetActuator.matchSingleOrder` to compute the amount of the "buy" token a party receives in exchange for consuming the counterparty's full remaining sell quantity: [2](#0-1) [3](#0-2) 

In each of these branches, `makerOrderCapsule`'s entire remaining sell balance is fully consumed (`setSellTokenQuantityRemain(0)`), while the amount of token the maker actually *receives* in return (`makerBuyTokenQuantityReceive`) is computed via `multiplyAndDivide`, which floors the true fractional ratio `makerSellRemainQuantity * makerBuyQuantity / makerSellQuantity`. Any fractional remainder produced by this division is not credited to the maker, the taker, or any fee pool — it is simply discarded. Because the maker's sell-side balance is debited in full but the buy-side proceeds are rounded down, the maker systematically receives strictly less value than the ratio implied by their own listed order price whenever the division is not exact.

The code comment at lines 467-474 even acknowledges this rounding behavior is relied upon ("it will get at least one buyToken... so if sellQuantity=200, buyQuantity=100..."), confirming the rounding-down design was deliberate but never compensated, unlike the bond-protocol's `debtDecayInterval` case where the correct behavior (per spec) required rounding up to avoid shortchanging the counterparty.

### Impact Explanation
This causes market makers (unprivileged, ordinary users who place limit sell orders on TRON's on-chain order-book market) to be paid less than the fair-value ratio of their own order whenever a taker's fill amount does not divide evenly into the maker's sell/buy quantities. This is a direct accounting/settlement underpricing defect analogous to the referenced bond-protocol bug: value is silently lost by the party whose order is being consumed, on every partial/uneven fill, with no attacker action required and no way for the maker to reclaim the lost fraction. Over many trades this constitutes a systematic, protocol-level value leak affecting real user funds (TRC10 tokens / TRX) held in the exchange/market module.

### Likelihood Explanation
This code path executes on essentially every market match where the exact ratio isn't an integer — which is common given arbitrary user-chosen `sellTokenQuantity`/`buyTokenQuantity` pairs, especially in the "taker == maker" and "taker > maker" branches where the maker's full remaining balance is consumed. No special privileges or attacker setup are required; it triggers naturally through everyday use of `MarketSellAssetActuator`.

### Recommendation
Round the settlement quantity in favor of the party whose remaining balance is being fully consumed (i.e., round up `makerBuyTokenQuantityReceive`/analogous quantities) instead of always flooring, or explicitly account for/return the rounding remainder rather than discarding it, consistent with how `ExchangeWithdrawActuator` validates precision loss via `RoundingMode.HALF_UP` checks elsewhere in the codebase. At minimum, change `multiplyAndDivide` (or add a variant) that ceils when computing amounts owed to a party whose order is being fully closed out.

### Proof of Concept
1. Maker places an order: sell 5 units of token A for 3 units of token B (`makerSellQuantity=5`, `makerBuyQuantity=3`).
2. Taker places an order that fully matches the maker's remaining 5 A, going into the `takerBuyTokenQuantityRemain == makerOrderCapsule.getSellTokenQuantityRemain()` branch:
   - `makerBuyTokenQuantityReceive = multiplyAndDivide(5, 3, 5) = floorDiv(15, 5) = 3` — exact, no loss in this instance.
3. Now consider `makerSellQuantity=5`, `makerBuyQuantity=2`, maker's remaining sell = 3:
   - Fair value: `3 * 2 / 5 = 1.2` B tokens owed to maker.
   - `multiplyAndDivide(3, 2, 5) = floorDiv(6, 5) = 1`.
   - Maker's sell-side balance is fully zeroed (`makerOrderCapsule.setSellTokenQuantityRemain(0)` at line 434), but maker receives only `1` B token instead of the fair `1.2`, permanently losing `0.2` B token worth of value with no compensating mechanism — mirroring the referenced bond-protocol's underpriced-decay bug pattern.

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-465)
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
```
