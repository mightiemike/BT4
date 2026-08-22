### Title
Division rounding lets a taker drain a maker's entire remaining TRC10 order for zero payment in `MarketSellAssetActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchSingleOrder` computes the counter-party's payout with a floor-dividing helper, `MarketUtils.multiplyAndDivide`, which truncates toward zero exactly like Solidity's integer division in the reported Buyout.sol bug. In the "taker == maker" exact-match branch this floor division can legitimately round down to `0`, and — unlike the sibling "taker > maker" branch, which explicitly guards against this — no zero-check exists here. A taker can craft a `MarketSellAssetContract` that exactly consumes a maker's remaining sell-token balance while the computed `makerBuyTokenQuantityReceive` rounds to zero, letting the taker take the maker's full remaining TRC10 tokens for free.

### Finding Description
`matchSingleOrder` computes three mutually exclusive branches depending on how a taker order compares to a maker order's remaining quantity: [1](#0-0) 

For the "taker == maker" branch: [2](#0-1) 

`makerBuyTokenQuantityReceive` is computed by `MarketUtils.multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity, ...)`, which performs `floorDiv(a*b, c)` — truncating, unrounded integer division: [3](#0-2) 

When `makerBuyQuantity` (maker's original "buy" quantity, i.e. price denominator side) is small relative to `makerSellQuantity`, and `makerSellRemainQuantity` (the maker's already-partially-filled remainder) is likewise small, `makerSellRemainQuantity * makerBuyQuantity` can be strictly less than `makerSellQuantity`, causing the division to floor to `0`. In that case:
- `makerOrderCapsule.setSellTokenQuantityRemain(0)` — the maker's entire remaining sell-token balance is marked as consumed.
- `addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive)` is later called with `0`, so the maker receives nothing in return.
- `takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain()` (the full remaining maker sell amount) is credited to the taker via `addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule)`.
- The taker's own remaining sell balance is decremented by `makerBuyTokenQuantityReceive` (`0`), so the taker pays nothing.

This is the exact same rounding-to-zero class as the reported Buyout.sol issue, only here the java-tron codebase already demonstrates awareness of it: the "taker > maker" branch explicitly guards against the identical zero-result case and refunds the maker instead of letting the trade complete at a zero price: [4](#0-3) 

The "taker == maker" branch lacks this guard entirely, so the asymmetric protection leaves a live free-asset-extraction path.

### Impact Explanation
An attacker acting purely as a permissionless taker (via a standard `MarketSellAssetContract` broadcast transaction) can obtain a victim maker's entire remaining TRC10/TRX order balance while paying nothing, provided the attacker can craft (or has previously driven, via earlier partial fills using the non-rounding "taker < maker" branch) the maker's remaining quantity `R`, sell quantity `S`, and buy quantity `B` such that `R*B < S` and can find an integer taker sell amount `T` with `floor(T*S/B) == R`. This is direct, unprivileged asset theft/accounting corruption inside TRON's on-chain exchange/market module — a concrete case of "asset or accounting corruption" via the exchange/market math bug class.

### Likelihood Explanation
Order books and remaining quantities are public on-chain state, so an attacker can select or engineer favorable `(S, B, R)` combinations (including by first partially filling the target order through ordinary trades using the exact/`taker < maker` branches, which do not round) until the exact-match branch is reachable with a zero-value maker payout. No special privileges, keys, or node compromise are required — only ordinary `MarketSellAssetContract` transactions.

### Recommendation
Apply the same protection used in the "taker > maker" branch to the "taker == maker" branch: after computing `makerBuyTokenQuantityReceive` via `MarketUtils.multiplyAndDivide`, check if it is `0` and, if so, refund/return the maker's remaining sell tokens instead of zeroing out `SellTokenQuantityRemain` with no payment (mirroring `MarketUtils.returnSellTokenRemain` handling used at lines 475-477). More robust fixes include: computing prices using `ceil` division (or 1-wei/1-unit minimum payout, similar to the external report's Solution B) so a matched trade never completes with a zero-value settlement for either side, or rejecting matches whose computed settlement amount is `0` before mutating order/account state.

### Proof of Concept
1. Victim places a maker sell order (`MarketSellAssetContract`): sell `S` units of asset `A`, buy `B` units of TRX, e.g. `S = 1,000,000`, `B = 1` (a low but valid A-for-TRX price).
2. Through prior ordinary trades (using the non-rounding "taker < maker" branch, lines 440-452), the attacker or market activity reduces the maker order's `SellTokenQuantityRemain` to some `R` where `R * B < S` (e.g. `R = 999,999` with `B = 1`, `S = 1,000,000`, so `floor(R*B/S) = 0`).
3. Attacker submits a taker `MarketSellAssetContract` selling TRX for asset `A`, choosing a sell amount `T` such that `takerBuyTokenQuantityRemain = MarketUtils.multiplyAndDivide(T, S, B, ...) == R` exactly (feasible for suitable `S,B` combinations, especially since `B` is attacker/maker chosen and `T` is fully attacker-controlled).
4. `matchSingleOrder` enters the `takerBuyTokenQuantityRemain == makerOrderCapsule.getSellTokenQuantityRemain()` branch (lines 418-439): `makerBuyTokenQuantityReceive = floor(R*B/S) = 0`.
5. The maker's order is fully consumed (`SellTokenQuantityRemain = 0`, state set `INACTIVE`), the maker is credited `0` TRX via `addTrxOrToken(makerOrderCapsule, 0)`, while the taker is credited the maker's full remaining `R` units of asset `A` via `addTrxOrToken(takerOrderCapsule, R, takerAccountCapsule)`, and the taker's own sell balance is decremented by `0` — the taker keeps their TRX and receives `R` units of `A` for free, at the maker's full expense.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-483)
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
    }
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
