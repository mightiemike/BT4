### Title
Unsafe `BigInteger.longValue()` truncation in `MarketUtils.multiplyAndDivide()` can corrupt DEX order-matching accounting - (File: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java)

### Summary
The reported bug class is an unsafe narrowing cast (`uint256`→`uint128` via `uint128(...)`) that truncates without reverting, causing incorrect accounting. The java-tron analog is `MarketUtils.multiplyAndDivide()`, which falls back to `BigInteger` arithmetic on overflow but converts the result back to `long` using the non-checked `longValue()` instead of the checked `longValueExact()`, silently truncating/wrapping any result that doesn't fit in a 64-bit `long`.

### Finding Description
`multiplyAndDivide(long a, long b, long c, boolean disableMath)` first attempts `multiplyExact`/`floorDiv`, and only on `ArithmeticException` (i.e., when `a*b` overflows `long`) falls back to `BigInteger` math: [1](#0-0) 

The fallback path computes `aBig.multiply(bBig).divide(cBig)` and returns `.longValue()`. Per the Java specification, `BigInteger.longValue()` performs a narrowing conversion that discards all but the lowest 64 bits and can silently produce a wrapped/negative/nonsensical value if the true mathematical result exceeds `Long.MAX_VALUE` — exactly the unsafe-downcast bug class described in the report, except here it's `BigInteger`→`long` rather than `uint256`→`uint128`.

This function is used by the on-chain DEX order matching logic in `MarketSellAssetActuator.matchSingleOrder()` to compute the exact quantity of tokens/TRX a taker or maker receives in a trade: [2](#0-1) [3](#0-2) [4](#0-3) 

Since `MarketOrderCapsule` quantities are `long` fields whose magnitude can approach `Long.MAX_VALUE` (~9.2×10^18) as set by user-submitted `MarketSellAssetContract`/`MarketOrder` orders, the product `a*b` computed as `BigInteger` can reach up to ~8.5×10^37. When `c` (the divisor, e.g. `makerBuyQuantity`/`makerSellQuantity`) is small relative to `a*b`, the quotient can still exceed `Long.MAX_VALUE`, and `longValue()` will return a truncated (potentially small, zero, or negative) result rather than throwing.

### Impact Explanation
The truncated/wrapped value returned by `multiplyAndDivide` directly determines `takerBuyTokenQuantityReceive` / `makerBuyTokenQuantityReceive`, which are then credited to accounts via `addTrxOrToken` (`addAssetAmountV2`/balance addition) and debited from order remainders (`subtractExact`). An incorrect (wrapped) quantity here means:
- Tokens/TRX can be minted or destroyed out of thin air within order matching (accounting corruption), since the amount deducted from one side's remaining balance is decoupled from the truncated amount credited to the other side.
- A negative wrapped `long` passed into `addExact`/`subtractExact` or `addAssetAmountV2` could cause unexpected exceptions (DoS on block processing) or, if not caught, incorrect balance changes.

This directly matches the "asset or accounting corruption" and "DoS via protocol implementation" impact categories, reachable purely by submitting `MarketSellAssetContract` broadcast transactions from any account (no privileged actor required) with sufficiently large order quantities to force the overflow path.

### Likelihood Explanation
Reaching the vulnerable path requires only that `a * b` (`long × long`) overflow 64 bits, which is achievable by any user placing market orders with large `sellTokenQuantity`/`buyTokenQuantity` values (bounded only by `long` range and possibly by asset-supply validation elsewhere, which does not prevent the multiplication itself from overflowing). Because the primary `multiplyExact` overflow check exists specifically to route into this fallback, the fallback is a normal, reachable code path — not a rare edge case — whenever quantities are large. However, whether an attacker can drive `aBig*bBig/cBig` past `Long.MAX_VALUE` in a real market (subject to actual token/TRX supply caps and exchange balance limits enforced elsewhere in validation) needs runtime confirmation; I could not fully verify all upstream quantity-bound checks in `MarketSellAssetActuator`'s `validate()` within the available context, so likelihood is assessed as plausible but not fully proven end-to-end.

### Recommendation
Replace `BigInteger.longValue()` with `BigInteger.longValueExact()` in `MarketUtils.multiplyAndDivide()` (chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java:276) so that any result exceeding the `long` range throws `ArithmeticException` instead of silently truncating, and ensure callers (`MarketSellAssetActuator`) handle/catch this exception by rejecting the trade rather than crashing or corrupting state — consistent with the pattern already used elsewhere in the codebase (e.g., `RepositoryImpl.divideCeilExact`, `ResourceProcessor.divideCeilExact`, `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, which already correctly use `longValueExact()`).

### Proof of Concept
Conceptual PoC (would require live-node or unit-test validation, not fully executed here):
1. Attacker A creates a sell order (`MarketSellAssetContract`) with `sellTokenQuantity` and an extreme `buyTokenQuantity` ratio close to `Long.MAX_VALUE`, forming the "maker" order.
2. Attacker B places a matching "taker" `MarketSellAssetContract` such that `takerSellRemainQuantity * makerSellQuantity` overflows 64-bit `long` (both operands near `Long.MAX_VALUE / 2` suffices), forcing `MarketUtils.multiplyAndDivide` into the `BigInteger` fallback branch.
3. Craft `makerBuyQuantity`/`makerSellQuantity` such that `aBig.multiply(bBig).divide(cBig)` exceeds `Long.MAX_VALUE`.
4. Observe that `matchSingleOrder` computes `takerBuyTokenQuantityReceive`/`makerBuyTokenQuantityReceive` as a wrapped/truncated `long` (via `MarketSellAssetActuator.java:402-404, 426-428, 461-463`), which is then credited via `addTrxOrToken`, producing an account balance inconsistent with the actual order quantities — i.e., value creation/destruction or an uncaught negative-amount balance mutation. [1](#0-0) [5](#0-4)

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L382-499)
```java
  // return all match or not
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

    MarketOrderDetail orderDetail = MarketOrderDetail.newBuilder()
        .setMakerOrderId(makerOrderCapsule.getID())
        .setTakerOrderId(takerOrderCapsule.getID())
        .setFillSellQuantity(makerBuyTokenQuantityReceive)
        .setFillBuyQuantity(takerBuyTokenQuantityReceive)
        .build();
    ret.addOrderDetails(orderDetail);
  }
```
