## Title
Silent truncation via `BigInteger.longValue()` in `MarketUtils.multiplyAndDivide` can corrupt token settlement amounts - (File: `chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java`)

### Summary
`MarketUtils.multiplyAndDivide` computes `a*b/c` for TRC10 exchange order matching. When the intermediate product overflows a Java `long`, the code falls back to `BigInteger` arithmetic but converts the final result back to `long` using `.longValue()` instead of `.longValueExact()`. Unlike the sibling `SafeExchangeProcessor` implementation (which strictly uses `longValueExact()` and would throw), this fallback path silently truncates the result to 64 bits with no bounds check — the same root-cause pattern as the referenced Sherlock finding, where a computed settlement amount is downcast to a smaller/bounded numeric type without any overflow guard, producing an incorrect settled amount instead of reverting. [1](#0-0) 

### Finding Description
`multiplyAndDivide` first attempts exact `long` multiplication via `multiplyExact`; if that throws `ArithmeticException` (i.e., `a*b` overflows a signed 64-bit long), it recomputes using `BigInteger`:

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

`BigInteger.longValue()` does not check for overflow — it discards all but the low-order 64 bits, potentially returning a wildly different (and possibly negative) value instead of throwing. This is functionally identical to the Sherlock report's unchecked `uint96(result.totalAmountIn)` cast: a value that legitimately exceeds the target primitive's range is truncated silently rather than rejected.

This function is used directly in TRC10 order-book matching to compute the actual quantity of tokens each side receives:

```java
long takerBuyTokenQuantityRemain = MarketUtils
    .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
        this.disableJavaLangMath());
...
makerBuyTokenQuantityReceive = MarketUtils
    .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
        this.disableJavaLangMath());
``` [2](#0-1) [3](#0-2) 

The three inputs (`sellTokenQuantity`, `buyTokenQuantity`, and their per-order remainders) are unprivileged-user-controlled `int64` values bounded only by a dynamic, governance-configurable `MarketQuantityLimit`:

```java
long quantityLimit = dynamicStore.getMarketQuantityLimit();
if (sellTokenQuantity > quantityLimit || buyTokenQuantity > quantityLimit) {
    throw new ContractValidateException("token quantity must less than " + quantityLimit);
}
``` [4](#0-3) 

Because `quantityLimit` is only bounded from above by a committee-settable parameter (not hard-coded to a small safe value), a user can craft `sellTokenQuantity`/`buyTokenQuantity` pairs whose product `a*b` overflows `long` (product exceeds `2^63-1`), forcing the `BigInteger` fallback. If the resulting quotient `(a*b)/c` still exceeds `Long.MAX_VALUE` (which can happen when `c` is small relative to `a*b`), `longValue()` truncates it into an unrelated, possibly negative, 64-bit value that is then used verbatim as the settled token amount transferred between the maker and taker accounts via `addAssetAmountV2`/`setBalance` in `MarketSellAssetActuator`.

### Impact Explanation
An incorrect settlement quantity computed this way directly corrupts on-chain TRC10/TRX accounting for both trade counterparties:
- If the truncated value is smaller than the correct value, the seller/maker suffers an under-payment (asset loss), analogous to the Sherlock report's "extreme loss for the auctioner."
- If truncation instead produces a larger or negative value, it can be leveraged to mint value out of the exchange mechanism at the counterparty's expense, or corrupt the invariant that `subtractExact`/`addExact` guards elsewhere in the same actuator rely on to prevent inconsistent balances.

This is a state-corruption/asset-accounting bug in a core financial primitive (TRC10 order-book market), matching the "accounting/settlement" impact category.

### Likelihood Explanation
Reachability requires the order's `sellTokenQuantity`/`buyTokenQuantity` fields (fully controlled by an unprivileged user, subject only to `getMarketQuantityLimit()`) to be large enough that `a*b` overflows `long` for at least one of the two `multiplyAndDivide` calls during matching. Whether this is practically exploitable today depends on the deployed value of `MarketQuantityLimit`; if it is kept very small (e.g., far below `~3×10^9`, the square root of `Long.MAX_VALUE`), `multiplyExact` never overflows and the vulnerable `BigInteger.longValue()` branch is never exercised. Confirming the current on-chain value of `MarketQuantityLimit` was not possible from the indexed code, so likelihood should be treated as configuration-dependent — but the code path itself contains no defense-in-depth (no `longValueExact()`, no explicit range check before returning), meaning any future increase of this governance parameter (or a chain that already sets it high) would silently reactivate the flaw with no additional code change needed.

### Recommendation
Replace `.longValue()` with `.longValueExact()` in the `BigInteger` fallback of `MarketUtils.multiplyAndDivide`, mirroring the stricter behavior already used in `SafeExchangeProcessor.exchangeFromSupply`, so that an out-of-range result throws an `ArithmeticException` (and is handled as a validation/execution failure) instead of silently corrupting settlement quantities. Additionally, consider tightening `MarketQuantityLimit` bounds or adding an explicit pre-check on `a*b` magnitude relative to `Long.MAX_VALUE` before returning from this utility.

### Proof of Concept
Conceptual PoC (cannot be executed against the index, but derivable from the code path shown above):
1. As an unprivileged user, create two TRC10 market sell orders where `sellTokenQuantity` and `buyTokenQuantity` are chosen near the configured `MarketQuantityLimit` such that their cross product in `matchSingleOrder`'s `multiplyAndDivide(a, b, c, ...)` call exceeds `Long.MAX_VALUE` (i.e., `a*b > 2^63-1`).
2. Choose `c` (the divisor, e.g., `makerBuyQuantity`) small enough that `(a*b)/c` still exceeds `Long.MAX_VALUE`.
3. Submit a matching `MarketSellAssetContract` transaction to trigger `matchSingleOrder`, which invokes `MarketUtils.multiplyAndDivide` at [2](#0-1) .
4. Observe that instead of throwing, the `BigInteger.longValue()` call at [5](#0-4)  returns a truncated/wrapped `long`, which is then applied as `takerBuyTokenQuantityReceive`/`makerBuyTokenQuantityReceive` to account balances via `addAssetAmountV2`, producing incorrect on-chain token balances for the trade participants.

Note: exact numeric inputs required to trigger the overflow depend on the live value of `DynamicPropertiesStore.getMarketQuantityLimit()`, which was not available in the indexed context; a Devin session with full repository/DB access would be needed to confirm the current parameter value and construct a concrete numeric PoC transaction.

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
