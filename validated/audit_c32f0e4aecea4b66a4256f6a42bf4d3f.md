## Title
Systematic rounding-down of maker consideration in order-matching favors the taker at the maker's expense - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchSingleOrder()` computes the amount of `buyToken` that a maker receives for having its remaining sell inventory consumed using `MarketUtils.multiplyAndDivide`, which performs integer floor division. The quantity the taker *receives* is set to the maker's exact remaining sell balance (no rounding), while the taker's *payment* to the maker is rounded down. This is the same rounding-direction defect as the referenced Caviar `buyQuote()` finding: the value that should be rounded up (an incoming consideration owed to the counterparty) is instead rounded down, systematically favoring the party that receives it and shorting the other side.

### Finding Description
In `matchSingleOrder()`, when a taker's order fully consumes a maker's remaining sell quantity ("taker == maker" and "taker > maker" branches): [1](#0-0) 

```java
} else {
  // taker > maker
  takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();
  makerBuyTokenQuantityReceive = MarketUtils
      .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
          this.disableJavaLangMath());
```

`takerBuyTokenQuantityReceive` is fixed to exactly `makerOrderCapsule.getSellTokenQuantityRemain()` — the taker always receives the maker's *full* remaining inventory. `makerBuyTokenQuantityReceive`, the consideration the maker should receive in exchange, is computed with `MarketUtils.multiplyAndDivide`: [2](#0-1) 

```java
public static long multiplyAndDivide(long a, long b, long c, boolean disableMath) {
  try {
    long tmp = multiplyExact(a, b, disableMath);
    return floorDiv(tmp, c, disableMath);
  } catch (ArithmeticException ex) {
    ...
  }
  ...
  return aBig.multiply(bBig).divide(cBig).longValue();
}
```

Both the primitive path (`floorDiv`) and the `BigInteger` fallback path truncate toward zero — i.e., always round down. Because the taker's output is fixed exactly (no rounding) while the maker's input (its required consideration) is rounded down, every partial match where `makerSellRemainQuantity * makerBuyQuantity` is not evenly divisible by `makerSellQuantity` transfers the fractional remainder from the maker to the taker for free. The same pattern occurs in the "taker == maker" branch: [3](#0-2) 

There is a guard for the case where `makerBuyTokenQuantityReceive == 0` (the case explicitly called out in the code comment), which reverts the fill and returns tokens to the maker — but this only protects the extreme case of a *fully* zero payment. It does not protect against non-zero but still short-changed payments, which occur far more frequently and are the exact class of issue flagged in the referenced report (rounding favors the receiver, not just the zero-payment edge case).

### Impact Explanation
Each partial match that hits a non-exact division silently transfers a fraction of a token unit of value from the maker to the taker, without maker consent and without any corresponding value increase elsewhere. This is asset/accounting corruption in the on-chain order-matching engine reachable by any account broadcasting `MarketSellAssetContract` transactions. While each individual rounding loss is bounded (less than 1 unit of the buy token), it is systematic and directionally biased (always favors the taker), so it can be repeatedly triggered against resting maker orders to accumulate value extraction over many trades — analogous to the "medium" impact rated in the original report, where LP/maker economics are degraded by the compounding one-sided rounding bias.

### Likelihood Explanation
High likelihood of occurrence: any two non-power-of-each-other maker/taker sell/buy quantities (which is the common case for arbitrary TRC10 token pairs and real-world order sizes) will trigger non-exact division in `multiplyAndDivide`. No privileged access is required — a taker only needs to submit an ordinary `MarketSellAssetContract` transaction that matches against an existing resting order whose price ratio doesn't divide evenly into the taker's remaining sell quantity.

### Recommendation
Round the maker's consideration (`makerBuyTokenQuantityReceive`) up, not down, when its counterparty (`takerBuyTokenQuantityReceive`) is fixed to a fully consumed quantity — i.e., use ceiling division for consideration owed to the counterparty, consistent with the principle in the referenced report that incoming/owed amounts should round up while amounts paid out at the disposal of the caller should round down. Concretely, change `MarketUtils.multiplyAndDivide` usage in the "taker == maker" and "taker > maker" branches to a ceiling-division variant (e.g., `(a*b + c - 1) / c`, guarding for overflow the same way the existing `BigInteger` fallback does), or otherwise re-derive `takerBuyTokenQuantityReceive` from the rounded-down `makerBuyTokenQuantityReceive` so the rounding loss is symmetric and doesn't systematically favor one side.

### Proof of Concept
Consider a maker order selling token A for token B with `makerSellQuantity = 3`, `makerBuyQuantity = 2` (i.e., a rate of 2/3 B per A), and remaining sell balance `makerSellRemainQuantity = 2`.

A taker submits an order that fully consumes this remaining maker balance ("taker > maker" branch):
- `takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain() = 2` (taker receives all 2 units of token A).
- `makerBuyTokenQuantityReceive = multiplyAndDivide(2, 2, 3) = floor(4/3) = 1` (maker only receives 1 unit of token B).

The fair (proportional) consideration for consuming the maker's full inventory is `4/3 ≈ 1.33` units of token B, but the maker only receives `1`, losing `0.33` units of value to the taker with no corresponding cost to the taker. Repeating this pattern across many partial fills accumulates a real, non-negligible transfer of value from makers to takers, entirely due to the asymmetric (floor-only) rounding in `MarketUtils.multiplyAndDivide`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L418-434)
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
