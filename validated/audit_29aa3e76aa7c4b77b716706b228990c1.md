### Title
Maker underpayment due to floor-division rounding in market order matching - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchSingleOrder` computes the amount of `buyToken` a resting order's owner (the "maker") is entitled to receive via `MarketUtils.multiplyAndDivide`, which performs `floorDiv` (round-towards-zero/down) division rather than round-up. This mirrors the Sherlock M-7 bug class: when the integer implementation of a proportional price/payout uses floor division instead of ceiling division, the party providing the reference price (here, the maker whose resting order defines the price) receives less than the exact proportional value, resulting in a systematic value transfer away from the maker.

### Finding Description
In `matchSingleOrder`, the maker's payout is computed as: [1](#0-0) 

and again in the "taker > maker" branch: [2](#0-1) 

Both calls delegate to `MarketUtils.multiplyAndDivide`, which explicitly floors the result: [3](#0-2) 

`makerBuyTokenQuantityReceive = makerSellRemainQuantity * makerBuyQuantity / makerSellQuantity` is the exact amount of `buyToken` owed to the maker for the `sellToken` it gives up, based on its own posted price ratio (`makerSellQuantity : makerBuyQuantity`). Because integer division truncates towards zero (floor), any non-terminating fractional remainder is silently discarded from the maker's payout — the maker always receives `⌊exact value⌋` instead of the fair `exact value` (or, per the whitepaper-analog reasoning applied in the Sherlock report, the ceiling that protects the price-setting party). The taker, in the symmetric case, benefits because the token it consumes from the maker (`makerOrderCapsule.getSellTokenQuantityRemain()`) is unaffected by this truncation, so the truncation loss falls entirely on the maker side of that specific match.

This is directly analogous to the reported bond issue: `BondBaseSDA._currentMarketPrice` used a floor `mulDiv` for computing the maker's effective sale price instead of `mulDivUp`, causing bond makers to sell below the true price. Here, `multiplyAndDivide`'s floor division in the resting-order payout calculation produces the same class of rounding-down loss for the liquidity-providing side (the maker) of a TRC10 exchange-order trade.

### Impact Explanation
Each match that produces a non-exact division causes the maker to receive strictly less `buyToken` than proportionally owed for the `sellToken` consumed, with the truncated remainder effectively vanishing from the system (neither party receives it, and no other actor is credited that dust as far as this function shows). Repeated over many trades, this constitutes a systematic value leak away from makers posting resting orders in the on-chain exchange/market functionality (`MarketSellAssetActuator`), which is reachable from any account broadcasting `MarketSellAssetContract` transactions (no privileged role required). Individual per-trade losses are at most 1 unit of the smallest token denomination, but this is a per-match rounding bias that always favors "not crediting" the maker fraction, matching the "loss for the makers" impact called out in the referenced report.

### Likelihood Explanation
Likelihood is high for triggering the rounding behavior itself (any order pair whose price ratio produces a non-integer quotient during matching will hit this path — which is the common case for arbitrary buy/sell quantities), but the magnitude per event is bounded to sub-unit dust, so it's a low-severity-but-frequent accounting bias rather than a single catastrophic exploit. It requires no special permissions — only submission of ordinary `MarketSellAssetContract` transactions that get matched against existing resting orders.

### Recommendation
Change `MarketUtils.multiplyAndDivide` (or introduce a dedicated ceiling-division variant) to round up when computing the amount owed to the maker (`makerBuyTokenQuantityReceive`), consistent with protecting the price-setting/resting-order side from being paid less than its posted price implies, mirroring the Bond Protocol fix of switching `mulDiv` to `mulDivUp` for maker-facing price/payout calculations. Care should be taken to preserve balance-conservation invariants (i.e., ensure the taker side is decremented consistently so total balances still net to zero, or that any residual dust is explicitly accounted for rather than silently dropped or double-allocated).

### Proof of Concept
Not independently executed against a running node; this is based on static code analysis of `MarketUtils.multiplyAndDivide` (lines 264-277) and its callers in `MarketSellAssetActuator.matchSingleOrder` (lines 402-463). Illustrative scenario: a maker posts a sell order for `makerSellQuantity = 3` of token A at `makerBuyQuantity = 2` of token B (i.e., price 2/3 B per A). A taker's order causes `makerSellRemainQuantity = 1` to be consumed (the "taker > maker" branch). The fair payout to the maker is `1 * 2 / 3 = 0.667`, which `multiplyAndDivide` floors to `0`. The code path even acknowledges this rounding effect in its own comments (`actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java:466-477`), showing the maker can receive `0` payout tokens for tokens it gave up — a case the codebase treats as an accepted "quantity too small" edge case, whereas the underlying floor-rounding cause is a systematic underpayment of makers described analogously in the referenced report as the whitepaper-mandated rounding-up requirement being violated. [4](#0-3)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L426-429)
```java
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L461-463)
```java
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L466-477)
```java
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
