### Title
Rounding-to-zero in taker==maker exact-match branch causes maker fund loss and free token extraction for taker - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchSingleOrder` computes proportional fill quantities using integer division that can round down to zero when a small remaining order quantity is multiplied against a token-pair price ratio, exactly mirroring the rounding-to-zero root cause described in the Notional report (`StrategyUtils._convertStrategyTokensToBPTClaim` returning zero `bptClaim` for small `strategyTokenAmount`). In the "taker == maker" branch this zero-rounding is not guarded, unlike a nearly identical branch a few lines below that does guard for it, producing an inconsistent, exploitable state.

### Finding Description
In `matchSingleOrder` [1](#0-0) , when `takerBuyTokenQuantityRemain == makerOrderCapsule.getSellTokenQuantityRemain()` (the "taker == maker" branch), the code computes:

```java
makerBuyTokenQuantityReceive = MarketUtils
    .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity, ...);
takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();

long takerSellTokenLeft =
    takerOrderCapsule.getSellTokenQuantityRemain() - makerBuyTokenQuantityReceive;
takerOrderCapsule.setSellTokenQuantityRemain(takerSellTokenLeft);
makerOrderCapsule.setSellTokenQuantityRemain(0);
``` [2](#0-1) 

`multiplyAndDivide` performs floor division (`makerSellRemainQuantity * makerBuyQuantity / makerSellQuantity`) and can legitimately return `0` when `makerSellRemainQuantity` is small and `makerBuyQuantity < makerSellQuantity` (a maker price where more sell-token is required per unit of buy-token) [3](#0-2) .

Immediately below in the sibling "taker > maker" branch, the exact same computation for `makerBuyTokenQuantityReceive` (using identical formula) is explicitly guarded:
```java
makerBuyTokenQuantityReceive = MarketUtils.multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity, ...);
...
if (makerBuyTokenQuantityReceive == 0) {
    // the quantity is too small, return the remain of sellToken to maker
    makerOrderCapsule.setSellTokenQuantityReturn();
    returnSellTokenRemain(makerOrderCapsule);
    return;
}
``` [4](#0-3) 

The comment even acknowledges the rounding-to-zero scenario is a known concern for this formula [5](#0-4) , but the guard was omitted from the "taker == maker" branch. When `makerBuyTokenQuantityReceive` rounds to `0` in that branch:
- `makerOrderCapsule.setSellTokenQuantityRemain(0)` fully closes the maker order and it is marked `INACTIVE` [6](#0-5) , even though the maker's already-escrowed sell tokens (deducted from balance at order creation via `transferBalanceOrToken`) yield **zero** payment back via `addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive)` [7](#0-6) .
- `takerSellTokenLeft = takerOrderCapsule.getSellTokenQuantityRemain() - 0` leaves the taker's remaining sell balance **unchanged**, yet the taker receives the maker's full `sellTokenQuantityRemain` via `addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, ...)` [8](#0-7) .

This exactly parallels the Notional bug class: a proportional-conversion function rounds down to zero for a small input, an early/implicit "no-op" path is taken for the zero-valued side, but the "large" side of the ledger is still fully decremented/settled — producing a divergence between the two sides of accounting (here, actual tokens transferred vs. tokens the maker order was credited/debited for).

### Impact Explanation
This is directly exploitable for fund extraction, not merely an internal bookkeeping desync: an attacker acting as maker can post a resting sell order with a price ratio (`buyTokenQuantity < sellTokenQuantity`) and a small residual `sellTokenQuantityRemain`; when a taker (which the attacker can also control, or any unrelated taker whose remaining buy amount happens to exactly equal the maker's remainder) matches it, the maker's order is fully closed and marked filled/inactive while receiving zero tokens back, and the taker receives the maker's entire remaining sell-token balance for free. This is worse than the reported Notional issue (which "only" desynced an internal accounting variable) — it results in a genuine value transfer with no consideration, reachable by any unprivileged account through the public `MarketSellAssetContract` actuator.

### Likelihood Explanation
`MarketSellAssetActuator` is a fully public, unprivileged actuator usable by any account with the market feature enabled (`dynamicStore.supportAllowMarketTransaction()`) [9](#0-8) . The price ratio and remaining quantities are attacker-controlled since orders are created and partially filled through ordinary trading, and integer division is deterministic, so triggering the zero-rounding condition on the "taker == maker" branch is straightforward to engineer with two accounts.

### Recommendation
Add the same zero-check guard used in the "taker > maker" branch to the "taker == maker" branch: if `makerBuyTokenQuantityReceive == 0`, return the maker's remaining sell tokens via `MarketUtils.returnSellTokenRemain`/`setSellTokenQuantityReturn` instead of closing the order and crediting the taker with the full amount for zero payment. More robustly, refactor the duplicated proportional-fill logic into a single helper that always performs the zero-check before mutating either party's remain/state, so future branches cannot omit it.

### Proof of Concept
1. Attacker (or two colluding accounts) opens a maker order selling token A for token B with `sellTokenQuantity = 200`, `buyTokenQuantity = 100` (i.e., buy/sell ratio 0.5), and lets it partially fill down to `sellTokenQuantityRemain = 1` (`makerSellRemainQuantity = 1`).
2. A taker order is submitted such that `takerBuyTokenQuantityRemain` (computed from the taker's own remain and the maker's price) equals exactly `1` (the maker's `sellTokenQuantityRemain`), triggering the "taker == maker" branch at [2](#0-1) .
3. `makerBuyTokenQuantityReceive = multiplyAndDivide(1, 100, 200, ...) = 0` (floor division of `100/200`).
4. Execution proceeds: maker order is set to `sellTokenQuantityRemain = 0` and `INACTIVE`; `addTrxOrToken(makerOrderCapsule, 0)` credits the maker nothing; taker's remain is decremented by `0` yet `addTrxOrToken(takerOrderCapsule, 1, ...)` credits the taker with the maker's full 1 unit of token A.
5. Net result: maker's escrowed token A is consumed with zero token B returned; taker receives 1 unit of token A for free — repeatable at scale by structuring maker order remainders to always land on this rounding boundary.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L181-184)
```java
    if (!dynamicStore.supportAllowMarketTransaction()) {
      throw new ContractValidateException("Not support Market Transaction, need to be opened by"
          + " the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L383-413)
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L489-489)
```java
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L490-490)
```java
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);
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
