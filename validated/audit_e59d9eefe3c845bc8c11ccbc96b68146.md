### Title
Silent long-overflow truncation in `MarketUtils.multiplyAndDivide` BigInteger fallback causes incorrect token settlement in TRC10 market order matching - (File: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java)

### Summary
`MarketUtils.multiplyAndDivide(long a, long b, long c, boolean disableMath)` computes `a * b / c` for TRC10 market-order matching. It first tries a checked `long` path (`multiplyExact`/`floorDiv`), but on `ArithmeticException` it falls back to `BigInteger` arithmetic and returns the result via `BigInteger.longValue()` instead of `BigInteger.longValueExact()`. `longValue()` silently truncates/wraps if the mathematically correct result does not fit into a `long`, exactly mirroring the Sherlock H-9 pattern of downcasting a computed total without an overflow check, leading to loss/corruption of settled amounts. [1](#0-0) 

### Finding Description
`multiplyAndDivide` is the core price-ratio calculation used to compute how much of the buy token a taker/maker receives during TRC10 market order matching in `MarketSellAssetActuator.matchSingleOrder`: [2](#0-1) [3](#0-2) [4](#0-3) 

The implementation:
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
``` [1](#0-0) 

When `a*b` overflows a signed 64-bit `long` (which is exactly the trigger condition for entering the fallback branch), the code correctly avoids overflow during multiplication by using `BigInteger`, but the final division result is coerced back to `long` with `.longValue()`. Unlike `.longValueExact()`, `BigInteger.longValue()` does **not** throw when the value doesn't fit into 64 bits — it takes the low-order 64 bits, i.e., it silently wraps/truncates. This is architecturally identical to the reported Sherlock issue: a summed/computed total is downcast to a narrower type without a bounds check, corrupting the settlement amount instead of failing safely.

Note that `a` and `c` (order remaining quantities and maker sell/buy quantities) are themselves bounded by `dynamicStore.getMarketQuantityLimit()` at order-creation validation time: [5](#0-4) 
This bound reduces — but does not eliminate — the practical likelihood of the `a*b` product exceeding `Long.MAX_VALUE * quotient` in a way that also causes `.longValue()` to truncate the final quotient (as opposed to merely overflowing the intermediate product, which the `BigInteger` path already safely handles). Whether `getMarketQuantityLimit()`'s configured value permits constructing `a`, `b` such that the true quotient `a*b/c` exceeds `Long.MAX_VALUE` could not be fully confirmed from the code inspected (its concrete default/configured numeric value was not retrieved in this session), so the exact reachable magnitude of `a`, `b`, `c` from an attacker-controlled `MarketSellAssetContract` remains partially unverified.

### Impact Explanation
If `takerBuyTokenQuantityRemain` or `makerBuyTokenQuantityReceive` is silently truncated to an incorrect (and potentially negative or drastically smaller) `long` value, the actuator uses that value directly to:
- Credit `addAssetAmountV2` / balance to the taker and maker accounts (`addTrxOrToken`), and
- Decrement `sellTokenQuantityRemain` for the counter-order via `subtractExact`.

This corrupts on-chain TRC10 token/TRX accounting for market participants: a party could receive a wildly wrong (potentially far smaller, non-matching, or negative-derived) payout amount relative to what the exchanged order actually earned, and remaining order-quantity bookkeeping (`setSellTokenQuantityRemain`) would become inconsistent with the truncated fill amount, producing balance/asset accounting corruption in a live TVM/actuator settlement path reachable from a normal signed transaction (`MarketSellAssetContract`), not requiring any privileged actor.

### Likelihood Explanation
The bug is only reachable in the rare situation where `a * b` (product of two order-quantity longs) exceeds `Long.MAX_VALUE`, which requires order quantities constrained by `dynamicStore.getMarketQuantityLimit()` to be large enough to trigger the `ArithmeticException` fallback, and further requires the actual `a*b/c` quotient (not just the intermediate product) to itself not fit in a signed 64-bit long. Because the final quotient (a token quantity actually credited to an account) exceeding `Long.MAX_VALUE` is an extreme edge case bounded by `getMarketQuantityLimit()`, likelihood is Low/Medium — this is a real correctness bug (unsafe downcast without bounds check, matching the bug class exactly) whose practical exploitability depends on the exact market quantity limit value configured in `DynamicPropertiesStore`, which was not fully confirmed in this session.

### Recommendation
Replace `.longValue()` with `.longValueExact()` in `MarketUtils.multiplyAndDivide`'s `BigInteger` fallback so an `ArithmeticException` is thrown (and can be caught/handled as a validation failure, consistent with how `AddExact`/`multiplyExact` overflow is already handled elsewhere in the actuator) instead of silently returning a wrapped/truncated result:
```java
return aBig.multiply(bBig).divide(cBig).longValueExact();
```
Additionally, verify/tighten `getMarketQuantityLimit()` bounds so that the maximum possible `a*b/c` result is provably representable in a `long`, and add an explicit unit test asserting `multiplyAndDivide` throws (rather than truncates) for inputs whose true quotient exceeds `Long.MAX_VALUE`.

### Proof of Concept
Not independently executed in this session; based on static analysis, a conceptual PoC:
1. Choose maker/taker TRC10 order quantities `a`, `b`, `c` (each ≤ `dynamicStore.getMarketQuantityLimit()`) such that `a * b` overflows a signed `long` (forcing the `BigInteger` fallback) and such that the mathematically correct quotient `(a*b)/c` itself exceeds `Long.MAX_VALUE`.
2. Submit two matching `MarketSellAssetContract` transactions (maker then taker) via broadcast so `MarketSellAssetActuator.matchSingleOrder` invokes `MarketUtils.multiplyAndDivide` with these values.
3. Observe that `BigInteger.longValue()` returns a truncated/wrapped (possibly negative or much smaller) result instead of throwing, and that this truncated value is used to credit `addAssetAmountV2`/balance and to adjust `sellTokenQuantityRemain`, producing incorrect settlement amounts on-chain.

Confirming step 1's exact numeric feasibility requires the concrete value of `dynamicStore.getMarketQuantityLimit()` (its default/configured value could not be retrieved within the tool-call budget of this session), so this PoC is a structural/code-level demonstration of the unsafe downcast rather than a confirmed end-to-end exploit trace.

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L227-230)
```java
    long quantityLimit = dynamicStore.getMarketQuantityLimit();
    if (sellTokenQuantity > quantityLimit || buyTokenQuantity > quantityLimit) {
      throw new ContractValidateException("token quantity must less than " + quantityLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L402-404)
```java
    long takerBuyTokenQuantityRemain = MarketUtils
        .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
            this.disableJavaLangMath());
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L426-428)
```java
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L461-463)
```java
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());
```
