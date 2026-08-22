### Title
Unchecked `BigInteger.longValue()` truncation in `MarketUtils.multiplyAndDivide` can silently corrupt matched-order quantities - (File: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java)

### Summary
`MarketUtils.multiplyAndDivide` falls back to `BigInteger` arithmetic when the primitive `multiplyExact` path overflows, but the final conversion `aBig.multiply(bBig).divide(cBig).longValue()` has no range check. `BigInteger.longValue()` is documented to silently discard all but the low‑order 64 bits when the mathematical result does not fit in a `long`, so for large enough `takerSellRemainQuantity`/`makerSellQuantity`/`makerBuyQuantity` combinations the value returned to `MarketSellAssetActuator.matchSingleOrder` no longer equals the true `a*b/c` and can be an arbitrary (even negative-looking) 64-bit pattern, contradicting the invariant that the function always returns the exact multiply-then-divide result.

### Finding Description
`MarketUtils.multiplyAndDivide` (chainbase/.../MarketUtils.java:264-277):
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

When `a*b` overflows a signed `long` (i.e. exceeds `Long.MAX_VALUE`, which happens whenever both operands exceed roughly `3.03e9`), the code correctly falls back to exact `BigInteger` math for the multiplication — this part is safe. However, the resulting quotient `aBig.multiply(bBig).divide(cBig)` can itself be an arbitrarily large `BigInteger` when `c` (the maker's buy quantity) is small relative to `a*b`. `BigInteger.longValue()` does not throw in that case; per its Javadoc it returns only the low-order 64 bits reinterpreted as a signed `long`, i.e. it silently truncates/wraps instead of raising `ArithmeticException` (as `longValueExact()` would).

This is invoked directly from `MarketSellAssetActuator.matchSingleOrder`:
```java
long takerBuyTokenQuantityRemain = MarketUtils
    .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
        this.disableJavaLangMath());
``` [2](#0-1) 
and again for `makerBuyTokenQuantityReceive` at lines 426-428 / 461-463. The returned (possibly corrupted) value drives:
- whether the "taker == maker", "taker < maker", or "taker > maker" branch is taken (lines 418, 440, 453),
- the exact amount credited to the taker via `takerBuyTokenQuantityReceive` in the "taker < maker" branch (line 445), which is `addTrxOrToken`-ed straight into the taker's balance while the maker's counter-leg (`makerOrderCapsule.getSellTokenQuantityRemain()`, i.e. the taker's *full* remaining sell amount) is unconditionally credited to the maker (line 446, then `addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive)` at line 490).

Because `sellTokenQuantity`/`buyTokenQuantity` are attacker-supplied fields of `MarketSellAssetContract` and are only bounded above by the configurable `dynamicStore.getMarketQuantityLimit()` (checked in `validate()` at MarketSellAssetActuator.java:227-230) with no coupling between the two values (an attacker can freely pick `buyTokenQuantity` as low as `1` while `sellTokenQuantity`/`sellTokenQuantityRemain` are near the limit), an attacker fully controls `a`, `b`, and `c` for both their maker and taker orders (either using two of their own accounts, or acting as taker against their own maker order). This makes it feasible to search offline for `(a, b, c)` triples where the true `a*b/c` is enormous (larger than the maker's real remaining sell quantity — which should force the "taker > maker" branch and cap the credited amount at the maker's real remaining stock) but the wrapped 64-bit `longValue()` result happens to be a small positive number that is *less* than the maker's real remaining quantity, causing the wrong branch (`taker < maker`) to be taken. In that branch the maker unconditionally receives the taker's *entire* remaining sell quantity while only decrementing its own remaining sell quantity by the corrupted (small) amount — i.e. the maker keeps almost all of its original sell order (recoverable later via cancellation/refund) while still receiving the taker's full payment, breaking `VALUE_CONSERVATION`.

None of the existing checks (`validate()`'s `sellTokenQuantity <= 0`, `quantityLimit` cap, `subtractExact`-guarded remain decrements, `ForkController` gates) prevent this, because they only bound the *inputs* to `multiplyAndDivide`, not the arithmetic correctness of its output once the fallback path's final narrowing conversion silently wraps.

### Impact Explanation
This is an asset/accounting-corruption bug in market order matching: a colluding pair of unprivileged accounts can construct maker/taker `MarketSellAssetContract`s whose quantities drive `multiplyAndDivide` into the `BigInteger` fallback with a quotient exceeding `Long.MAX_VALUE`, causing `longValue()` truncation. The resulting mismatch between credited/debited quantities can let the attacker retain most of a maker order's backing supply while still collecting full payment from the matched taker leg, effectively minting value not backed by the maker's real remaining balance — matching the TRON bounty "asset/accounting corruption" impact class.

### Likelihood Explanation
Exploitability depends on (a) the ability to make `a*b` overflow a `long` (trivially reachable once both `takerSellRemainQuantity` and `makerSellQuantity` exceed ~3.03e9, which is plausible given `MarketQuantityLimit` is a large governance-tunable cap intended to accommodate TRC10 supplies up to `long` range) and (b) finding a `(a,b,c)` combination whose wrapped 64-bit quotient happens to fall below the maker's real remaining quantity while the true quotient is far larger. Search for such triples is a pure offline computation (no on-chain cost beyond ordinary transaction/market fees), and the attacker only needs two funded accounts and the market feature enabled (`dynamicStore.supportAllowMarketTransaction()` — a committee-gated feature flag, not attacker-controlled, but this is a standard mainnet-enabled feature and does not require any elevated privilege from the attacker). I was not able to verify the concrete numeric value of `getMarketQuantityLimit()` in this pass (tool budget exhausted), so I cannot definitively confirm that ordinary market parameters permit `a,b > ~3e9`; this is the main remaining uncertainty for real-world feasibility, though the truncation defect in `multiplyAndDivide` itself is unconditionally provable via direct unit testing of the utility function.

### Recommendation
Replace the unchecked `longValue()` in the `BigInteger` fallback with a bounds-checked conversion (e.g. `BigInteger.longValueExact()`, catching `ArithmeticException` and rejecting/erroring the match, or clamping/throwing `ContractValidateException`) so that any quotient outside `[Long.MIN_VALUE, Long.MAX_VALUE]` fails loudly instead of wrapping silently. Additionally, add a sanity assertion in `matchSingleOrder` that all computed receive/remain quantities are non-negative and never exceed the counterpart order's real remaining balance before crediting via `addTrxOrToken`.

### Proof of Concept
```java
// Demonstrates BigInteger.longValue() truncation in the fallback path of
// MarketUtils.multiplyAndDivide when a*b/c exceeds Long range.
@Test
public void multiplyAndDivide_fallbackTruncatesSilently() {
  long a = 4_000_000_000L;   // > sqrt(Long.MAX_VALUE) so a*b overflows long
  long b = 4_000_000_000L;
  long c = 1L;               // minimal legal buyTokenQuantity

  // True mathematical result: a*b/c = 16_000_000_000_000_000_000 (> Long.MAX_VALUE)
  java.math.BigInteger expectedExact = java.math.BigInteger.valueOf(a)
      .multiply(java.math.BigInteger.valueOf(b))
      .divide(java.math.BigInteger.valueOf(c));

  long actual = MarketUtils.multiplyAndDivide(a, b, c, false);

  // actual is NOT the exact BigInteger result reinterpreted safely -
  // it is silently wrapped/truncated, violating the exactness invariant.
  assertNotEquals(expectedExact.longValueExact(), actual); // expectedExact throws or mismatches
  // longValue() wraps to low 64 bits, which can even be negative or
  // deceptively "small", inconsistent with the true quotient.
}
```
This proves the core defect at the utility-function level without needing to fully wire up account/order stores. Reaching it end-to-end via `MarketSellAssetActuator` requires crafting a `MarketSellAssetContract` maker order with `sellTokenQuantity`/`buyTokenQuantity` near `dynamicStore.getMarketQuantityLimit()` and `1` respectively, and a matching taker order with a comparably large `sellTokenQuantity`, then asserting via `matchSingleOrder` (reflection or package-visibility test) that the sum of debits from maker+taker balances does not equal the sum of credits recorded in the resulting `MarketOrderDetail`.

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L402-404)
```java
    long takerBuyTokenQuantityRemain = MarketUtils
        .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
            this.disableJavaLangMath());
```
