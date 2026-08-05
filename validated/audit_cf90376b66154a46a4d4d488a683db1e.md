### Title
Rounding Error in Market Order Full-Match Branch Allows Draining Maker's Remaining Tokens Without Payment - ([File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java])

### Summary
`MarketSellAssetActuator.matchSingleOrder` computes the taker/maker settlement amounts using integer division that rounds down (`MarketUtils.multiplyAndDivide`, which uses `floorDiv`). In the "taker == maker" (full-match) branch, the amount the maker receives (`makerBuyTokenQuantityReceive`) is computed the same way as in the "taker > maker" branch, but unlike that branch, **no zero-amount check is performed**. This lets a taker fully consume a maker's remaining sell-token balance while paying the maker `0` in return, exactly mirroring the "rounding error lets input be zero while output is non-zero" bug class described in the external report.

### Finding Description
`matchSingleOrder` computes settlement amounts via `MarketUtils.multiplyAndDivide`, which performs `floorDiv(a*b, c)`: [1](#0-0) 

In the three branches of `matchSingleOrder`:
- "taker < maker": no rounding hazard for `makerBuyTokenQuantityReceive` (it is simply the taker's original quantity, exact).
- "taker > maker": `makerBuyTokenQuantityReceive` is computed via `multiplyAndDivide` and explicitly checked; if it rounds to `0`, the code returns the maker's remaining sell tokens back to the maker instead of letting the taker take them for free: [2](#0-1) 
- "taker == maker" (exact full match): `makerBuyTokenQuantityReceive` is computed with the exact same rounding-prone formula, but **no zero check exists**. The taker unconditionally receives the maker's entire remaining sell quantity (`takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain()`), while the maker is credited with `makerBuyTokenQuantityReceive`, which can legitimately be `0`: [3](#0-2) 

Concretely, for a maker order with `sellTokenQuantity = 100`, `buyTokenQuantity = 99` and remaining sell quantity `M = 1` (reachable after prior partial fills whittle the order down), a taker selling `T = 1` unit triggers:
- `takerBuyTokenQuantityRemain = floor(1*100/99) = 1 = M` → enters the "taker == maker" branch.
- `makerBuyTokenQuantityReceive = floor(M*99/100) = floor(0.99) = 0`.

The taker receives the maker's entire remaining `1` unit of the sell token (`addTrxOrToken(takerOrderCapsule, 1, ...)`), the maker receives `0` (`addTrxOrToken(makerOrderCapsule, 0)`), and the taker's own order is left effectively unconsumed (`takerSellTokenLeft = T - 0 = 1`, i.e. the taker's paying asset is untouched). The maker's asset is transferred out for zero consideration.

The developer's own comment on the sibling "taker > maker" branch ("the quantity is too small, return the remain of sellToken to maker") shows the rounding-to-zero hazard was recognized and mitigated there, but the identical hazard in the "taker == maker" branch was left unguarded — an omission of exactly the missing `token_amount_in > 0` style check described in the external report.

### Impact Explanation
This allows an unprivileged attacker acting as a market taker to drain a maker's TRC10/TRX order balance without paying the corresponding amount, by crafting (or waiting for/inducing via repeated small partial fills) a maker order whose remaining quantity lands on a rounding-favorable boundary relative to its price ratio, then submitting a precisely sized `MarketSellAssetContract` to hit the exact-match branch. This is a concrete on-chain asset-accounting/settlement bug (value extraction from another user's live order) reachable by any account with `AllowMarketTransaction` enabled — not a privileged or mocked-only path.

### Likelihood Explanation
Exploitation requires the attacker to control only their own taker order size and target maker orders whose remaining quantity/price ratio satisfies `M * buyTokenQuantity < sellTokenQuantity` at the moment of an exact-match trade. An attacker can either wait for organically-partially-filled orders to reach such a remainder, or actively engineer it by first partially filling a maker order (matches are attacker-controlled since `sellTokenQuantity`/`buyTokenQuantity` are attacker-chosen when creating their own taker orders) down to a favorable remainder, then submitting the final exact-size taker order. This is deterministic arithmetic, not probabilistic, so likelihood is high once a suitable maker order is identified or induced.

### Recommendation
In the "taker == maker" branch of `matchSingleOrder`, add the same zero-amount guard used in the "taker > maker" branch: if `makerBuyTokenQuantityReceive == 0` (or more strictly, if it does not meet a minimum), reject/return the maker's remaining sell tokens to the maker (or reject the trade) instead of allowing the taker to receive the maker's full remaining balance for zero payment. Consider unifying the settlement calculation into a single helper with a single, consistently enforced non-zero check across all three branches.

### Proof of Concept
1. Enable market transactions (`AllowMarketTransaction`).
2. Attacker (as maker candidate or by using two of its own/collaborator accounts) creates a maker order: `sellTokenId=A, sellTokenQuantity=100, buyTokenId=TRX, buyTokenQuantity=99` (owner = victim).
3. Through prior partial fills (achievable by the attacker submitting smaller taker orders against this same maker order), reduce `sellTokenQuantityRemain` to exactly `1`.
4. Attacker submits `MarketSellAssetContract{ sellTokenId=TRX, sellTokenQuantity=1, buyTokenId=A, buyTokenQuantity=... }` sized so `takerSellRemainQuantity=1`.
5. In `matchSingleOrder`: `takerBuyTokenQuantityRemain = floor(1*100/99) = 1`, equal to `makerSellRemainQuantity=1` → "taker == maker" branch taken.
6. `makerBuyTokenQuantityReceive = floor(1*99/100) = 0`.
7. `addTrxOrToken(takerOrderCapsule, 1, ...)` credits the attacker with `1` unit of token A; `addTrxOrToken(makerOrderCapsule, 0)` credits the victim maker with nothing; the attacker's own sell-side balance (`takerSellTokenLeft = 1 - 0 = 1`) remains unspent in their order, confirmed via test assertions analogous to those in `MarketSellAssetActuatorTest` (e.g. `matchAll2SamePriceBuyOrders1`, `partMatchMakerBuyOrders1`) which already exercise balance assertions for these code paths. [4](#0-3)

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-482)
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
```
