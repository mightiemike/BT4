I found a strong analog. Let me verify the exact overflow-prone path in `MarketUtils.multiplyAndDivide`.### Title
Silent BigInteger→`long` Truncation in Market Order Matching Causes Incorrect Token Settlement - (File: `chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java`)

### Summary
`MarketUtils.multiplyAndDivide()`, used by `MarketSellAssetActuator` to compute settlement quantities during TRC10 market order matching, falls back to `BigInteger` arithmetic when the fast-path `long` multiplication overflows, but converts the `BigInteger` result back to `long` using `.longValue()` instead of `.longValueExact()`. This mirrors the reported `int256`→`int64` downcast bug: a wider-precision computed value is silently truncated/wrapped into a narrower type without any bounds check, producing an incorrect (potentially negative or wildly wrong) settlement amount that is then credited to a user's balance/asset holdings.

### Finding Description
In `chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java`:

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

The fast path correctly detects overflow via `multiplyExact`/`floorDiv` (which throw `ArithmeticException` on overflow) and falls back to `BigInteger` for full precision — exactly the intent behind the reported Solidity fix (use wider precision consistently). However, the final conversion of the `BigInteger` result back to `long` uses the unchecked `.longValue()`, which — like the Solidity `int64(int256(...))` downcast in the report — silently truncates/wraps the value instead of throwing when the true mathematical result exceeds `Long.MAX_VALUE`/`Long.MIN_VALUE`. Elsewhere in the same codebase the pattern is done correctly with `.longValueExact()` (which throws `ArithmeticException` on truncation), e.g. `RepositoryImpl.divideCeilExact`, `RepositoryImpl.getUsage` (hardened), `Wallet.checkPublicAmount`, and `SafeExchangeProcessor.exchangeFromSupply` all use `.longValueExact()` — demonstrating the project's own established, intended safeguard that `multiplyAndDivide` fails to follow. [2](#0-1) [3](#0-2) 

`multiplyAndDivide` is called directly from `MarketSellAssetActuator.matchSingleOrder()` to compute the exact token amounts exchanged between taker and maker orders:

```java
long takerBuyTokenQuantityRemain = MarketUtils
    .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
        this.disableJavaLangMath());
...
makerBuyTokenQuantityReceive = MarketUtils
    .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
        this.disableJavaLangMath());
``` [4](#0-3) [5](#0-4) 

The resulting values are then directly credited to account balances/asset holdings via `addTrxOrToken`, which calls `addExact`/`addAssetAmountV2`:

```java
private void addTrxOrToken(MarketOrderCapsule orderCapsule, long num,
    AccountCapsule accountCapsule) {
  byte[] buyTokenId = orderCapsule.getBuyTokenId();
  if (Arrays.equals(buyTokenId, "_".getBytes())) {
    accountCapsule.setBalance(addExact(accountCapsule.getBalance(), num));
  } else {
    accountCapsule.addAssetAmountV2(buyTokenId, num, dynamicStore, assetIssueStore);
  }
}
``` [6](#0-5) 

Because `num` here is a `long` that has already been silently truncated from an out-of-range `BigInteger`, the downstream `addExact`/overflow-safe accounting cannot detect the corruption — the wrong value has already been baked in before it ever reaches the "safe" arithmetic wrappers.

### Impact Explanation
Market order `sellTokenQuantity`/`buyTokenQuantity` fields are `int64` and fully attacker-controlled via `MarketSellAssetContract`, up to `Long.MAX_VALUE` (`9,223,372,036,854,775,807`), which is easily achievable for TRC10 tokens (which frequently use high supply/precision, unlike TRX's fixed decimals). By crafting maker/taker order quantities such that `a * b` overflows a signed 64-bit `long` (triggering the `BigInteger` fallback) and such that `(a*b)/c` still exceeds `Long.MAX_VALUE`, the truncating `.longValue()` call produces an arbitrary (including negative) settlement quantity. This value is then unconditionally added to a user's TRX balance or TRC10 asset balance in `addTrxOrToken`, resulting in incorrect accounting: a user could receive a corrupted (potentially far larger, wrapped-to-negative, or otherwise wrong) amount of tokens/TRX relative to what they actually traded, breaking the fundamental settlement invariant of the exchange and enabling fabrication or destruction of asset balances at the protocol level.

### Likelihood Explanation
The path is reachable by any unprivileged user through the public `MarketSellAsset` transaction type, which creates and matches TRC10 orders without special permissions. Triggering the fast-path overflow only requires two order quantities whose product exceeds `Long.MAX_VALUE` (trivial given quantities up to `Long.MAX_VALUE`); the second condition, that the true `BigInteger` quotient still exceeds `Long.MAX_VALUE` after fallback, requires `c` (the divisor quantity) to be small relative to `a*b`, which is achievable by choosing maker/taker quantities with a large price ratio. This is a purely computational trigger requiring no special privileges, race conditions, or governance-level state; it only requires posting two orders with carefully chosen quantities.

### Recommendation
Change the fallback in `multiplyAndDivide` to use `.longValueExact()` instead of `.longValue()`, and propagate/handle the resulting `ArithmeticException` (e.g., reject the match or clamp/validate order quantities at contract validation time) so that out-of-range results cause a controlled failure rather than a silent, incorrect settlement amount — consistent with the exact-conversion pattern already used elsewhere in the codebase (`RepositoryImpl`, `SafeExchangeProcessor`, `Wallet.checkPublicAmount`).

### Proof of Concept
1. Attacker A creates a sell order via `MarketSellAssetContract`: `sellTokenQuantity = A_SELL` (TRX), `buyTokenId = TOKEN`, `buyTokenQuantity = A_BUY`, chosen so it becomes the resting "maker" order with `makerSellQuantity = TOKEN` amount and `makerBuyQuantity` a small TRX amount (to create a very high token-per-TRX price ratio).
2. Attacker (or colluding account) B posts a matching taker sell order with `sellTokenQuantity = B_SELL` chosen such that `takerSellRemainQuantity * makerSellQuantity` exceeds `Long.MAX_VALUE` (overflowing the `multiplyExact` fast path in `multiplyAndDivide`, per `MarketSellAssetActuator.matchSingleOrder` line 402-404) and such that the true (unwrapped) quotient `(takerSellRemainQuantity * makerSellQuantity) / makerBuyQuantity` still exceeds `Long.MAX_VALUE`.
3. `multiplyAndDivide` falls back to `BigInteger` computation and returns `aBig.multiply(bBig).divide(cBig).longValue()`, silently truncating the oversized result into an arbitrary/negative 64-bit value.
4. This truncated value (`takerBuyTokenQuantityReceive`/`makerBuyTokenQuantityReceive`) is passed into `addTrxOrToken`, crediting attacker B's account with an incorrect token or TRX amount inconsistent with the actual trade economics — corrupting account balance/asset accounting. [7](#0-6)

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

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L944-951)
```java
  private long divideCeilExact(BigInteger numerator, BigInteger denominator) {
    BigInteger[] divRem = numerator.divideAndRemainder(denominator);
    long result = divRem[0].longValueExact();
    if (divRem[1].signum() > 0) {
      result = StrictMathWrapper.addExact(result, 1);
    }
    return result;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L30-38)
```java
  private long exchangeFromSupply(long balance, BigDecimal supplyQuant) {
    BigDecimal bdBalance = BigDecimal.valueOf(balance);
    BigDecimal base = BigDecimal.ONE.add(
        supplyQuant.divide(SUPPLY, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 2000.0);
    BigDecimal exchangeBalance = bdBalance.multiply(
        BigDecimal.valueOf(powResult).subtract(BigDecimal.ONE));
    return exchangeBalance.setScale(0, RoundingMode.DOWN).longValueExact();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L383-490)
```java
  private void matchSingleOrder(MarketOrderCapsule takerOrderCapsule,
      MarketOrderCapsule makerOrderCapsule, TransactionResultCapsule ret,
      AccountCapsule takerAccountCapsule)
      throws ItemNotFoundException {

    long takerSellRemainQuantity = takerOrderCapsule.getSellTokenQuantityRemain();
    long makerSellQuantity = makerOrderCapsule.getSellTokenQuantity();
    long makerBuyQuantity = makerOrderCapsule.getBuyTokenQuantity();
    long makerSellRemainQuantity = makerOrderCapsule.getSellTokenQuantityRemain();

    // according to the price of maker, calculate the quantity of taker can buy
    // for makerPrice,sellToken is A,buyToken is TRX.
    // for takerPrice,buyToken is A,sellToken is TRX.

    // makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX =
    //   takerBuyTokenQuantityCurrent_A/takerSellTokenQuantityRemain_TRX
    // => takerBuyTokenQuantityCurrent_A = takerSellTokenQuantityRemain_TRX *
    //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX

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

    long takerBuyTokenQuantityReceive; // In this match, the token obtained by taker
    long makerBuyTokenQuantityReceive; // the token obtained by maker

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
    }

    // save makerOrderCapsule
    orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule);

    // add token into account
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L538-548)
```java
  private void addTrxOrToken(MarketOrderCapsule orderCapsule, long num,
      AccountCapsule accountCapsule) {

    byte[] buyTokenId = orderCapsule.getBuyTokenId();
    if (Arrays.equals(buyTokenId, "_".getBytes())) {
      accountCapsule.setBalance(addExact(accountCapsule.getBalance(), num));
    } else {
      accountCapsule
          .addAssetAmountV2(buyTokenId, num, dynamicStore, assetIssueStore);
    }
  }
```
