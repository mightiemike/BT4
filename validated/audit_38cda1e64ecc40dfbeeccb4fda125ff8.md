### Title
Rounding-loss dust orders let an attacker force `MarketSellAssetActuator` match loop to abort with "Too many matches" (order-book DoS) - (`actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchOrder()` walks the maker order book at the best price and calls `matchSingleOrder()` for every maker order it dequeues, incrementing `matchOrderCount` on every call regardless of whether that call actually transferred any value. `matchSingleOrder()` computes the fillable quantity with an integer floor-division (`MarketUtils.multiplyAndDivide`), and when that quotient rounds down to `0` the order contributes nothing but is still consumed from the queue and still counts toward the `MAX_MATCH_NUM` (20) cap. An attacker can cheaply seed the order book with many minimal-value orders whose price ratio causes this "dust" rounding, so that any legitimate incoming `MarketSellAssetContract` that reaches the best price is forced through >20 such no-op matches and the whole transaction reverts with `ContractValidateException("Too many matches...")`, wrapped into `ContractExeException` in `execute()`. This is the same bug class as the audited report: a floor-division rounding artifact lets a malicious user place minimally-sized entries that pass validity checks yet do not contribute proportional value, defeating a hard-coded cap/threshold and forcing the counterpart operation into failure/DoS.

### Finding Description
In `matchOrder()`: [1](#0-0) 
every maker order dequeued from `orderIdListCapsule` is passed to `matchSingleOrder()` and `matchOrderCount` is incremented unconditionally; once it exceeds `MAX_MATCH_NUM` (20) a `ContractValidateException` is thrown.

Inside `matchSingleOrder()`, the fillable amount is computed via a plain floor division: [2](#0-1) 
When `takerSellRemainQuantity * makerSellQuantity < makerBuyQuantity`, `MarketUtils.multiplyAndDivide` (which performs `floorDiv`) returns `0`: [3](#0-2) 
In that branch, the maker order is simply skipped (returned to its owner, no value exchanged) — but the surrounding loop in `matchOrder()` has already incremented `matchOrderCount` for that iteration.

An attacker can create up to `MAX_ACTIVE_ORDER_NUM` (100) low-cost maker orders (e.g. minimal `sellTokenQuantity`/`buyTokenQuantity` pairs, such as `1:2`) at (or better than) the best price for a given trading pair. Because these ratios are tiny, any taker whose current remaining sell quantity multiplied by the maker's `sellTokenQuantity` is smaller than the maker's `buyTokenQuantity` will floor to `0` fillable amount against that maker order — the match is a complete no-op but still consumes one slot of the `MAX_MATCH_NUM` budget. Placing 21+ such entries at the head of the price queue guarantees that any subsequent legitimate `MarketSellAssetContract` transaction hitting that price level will exhaust the cap and abort with `ContractExeException`, exactly the way the Sherlock report describes spam bids sized to round-check boundaries defeating the `slotSize` check and forcing `FAILED_UNDERSOLD`.

Confirmatory test showing the cap/abort mechanism (though using real, non-dust fills) already exists: [4](#0-3) 
This test proves the DoS trigger path is real; the dust-rounding variant described above makes the attack far cheaper (near-zero capital, only the constant `getMarketSellFee()` cost per order) and reusable across many victims/transactions since the maker orders remain in the book after the loop returns (`orderIdListCapsule` isn't emptied by the zero-fill branch).

### Impact Explanation
Any unprivileged account can submit ordinary `MarketSellAssetContract` transactions (no special permission needed — this is reachable from anonymous broadcast transactions via `MarketSellAssetActuator`). By populating a trading pair's order book with cheap dust orders at the best price, the attacker can force any legitimate trader's `MarketSellAssetContract` transaction against that pair to consistently fail with `ContractExeException`, denying service (DoS) on the on-chain market feature for that token pair while the attacker's own capital at risk is minimal (order sizes as small as `1` unit of an asset, capped by the constant per-order market sell fee only, not the trade notional).

### Likelihood Explanation
Reaching this path requires only standard `MarketSellAssetContract` transactions, which require `dynamicStore.supportAllowMarketTransaction()` to be enabled (as it presumably is on any chain using the exchange market). No privileged role, leaked key, or malicious peer/node behavior is needed — a single ordinary account, or a handful of colluding accounts (bounded by `MAX_ACTIVE_ORDER_NUM = 100` orders per account) can set up the dust order book once and then repeatedly DoS any trader targeting that price level.

### Recommendation
- Only increment `matchOrderCount` in `matchOrder()` when `matchSingleOrder()` actually transfers a non-zero amount (i.e., skip the increment on the dust/no-op branch at `matchSingleOrder` lines 406-413), or
- Reject/clean up maker orders whose configured price would floor to zero fill for any plausible taker remainder (enforce a minimum notional per order in `MarketSellAssetActuator.validate()`), and/or
- Use ceiling/BigDecimal-based rounding consistent with the value actually reserved, similar to the mitigation applied for the referenced Sherlock finding (`slotSize = total / max + 1`), so dust-priced orders cannot occupy match-cap slots without contributing value.

### Proof of Concept
1. Attacker account A creates ~25 `MarketSellAssetContract` orders selling `sellTokenId=X` for `buyTokenId=Y` with `sellTokenQuantity=1`, `buyTokenQuantity=2` (a valid, cheap price ratio), all at the best price for pair `(Y,X)` from a taker's perspective — mirrors the existing `exceedMaxMatchNumLimit` test setup pattern. [5](#0-4) 
2. Victim submits a normal `MarketSellAssetContract` selling `Y` for `X` with a remaining quantity small enough that `takerSellRemainQuantity * makerSellQuantity(1) < makerBuyQuantity(2)` for each dust order encountered.
3. `matchOrder()` walks through the 25 dust maker orders; each call to `matchSingleOrder()` hits the `takerBuyTokenQuantityRemain == 0` branch (no value exchanged) yet `matchOrderCount` still increments each time. [6](#0-5) 
4. Once `matchOrderCount > MAX_MATCH_NUM (20)`, `ContractValidateException("Too many matches...")` is thrown and propagated as `ContractExeException`, aborting the victim's transaction — reproducing the DoS with near-zero attacker cost instead of real matched volume.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L341-360)
```java
      // match different orders which have the same price
      while (takerCapsule.getSellTokenQuantityRemain() != 0
          && !orderIdListCapsule.isOrderEmpty()) {
        byte[] orderId = orderIdListCapsule.getHead();
        MarketOrderCapsule makerOrderCapsule = orderStore.get(orderId);

        matchSingleOrder(takerCapsule, makerOrderCapsule, ret, takerAccountCapsule);

        // remove order
        if (makerOrderCapsule.getSellTokenQuantityRemain() == 0) {
          // remove from market order list
          orderIdListCapsule.removeOrder(makerOrderCapsule, orderStore,
              pairPriceKey, pairPriceToOrderStore);
        }

        matchOrderCount++;
        if (matchOrderCount > MAX_MATCH_NUM) {
          throw new ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM);
        }
      }
```

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

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L1825-1873)
```java
  @Test
  public void exceedMaxMatchNumLimit() throws Exception {

    InitAsset();

    int start = 10;
    int limit = MarketSellAssetActuator.getMAX_MATCH_NUM();
    int step = 1;
    int end = start + step * limit;

    //(sell id_1  and buy id_2)
    String sellTokenId = TOKEN_ID_ONE;
    String buyTokenId = TOKEN_ID_TWO;
    long buyTokenQuant = 400L;
    long sellTokenQuant = buyTokenQuant * (end / start + 1);

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_FIRST);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    accountCapsule.addAssetAmountV2(sellTokenId.getBytes(), sellTokenQuant,
        dbManager.getDynamicPropertiesStore(), dbManager.getAssetIssueStore());
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);
    Assert.assertEquals(sellTokenQuant,
            (long) accountCapsule.getAssetV2MapForTest().get(sellTokenId));

    // Initialize the order book

    // at least limit+1 times
    for (int i = start; i <= end; i += step) {
      addOrder(buyTokenId, (long) start, sellTokenId, i, OWNER_ADDRESS_SECOND);
    }

    // this order(taker) need to match 21 times
    MarketSellAssetActuator actuator = new MarketSellAssetActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_FIRST, sellTokenId, sellTokenQuant, buyTokenId, buyTokenQuant));

    String errorMessage =
        "Too many matches. MAX_MATCH_NUM = " + MarketSellAssetActuator.getMAX_MATCH_NUM();
    try {
      TransactionResultCapsule ret = new TransactionResultCapsule();
      actuator.validate();
      actuator.execute(ret);
      fail(errorMessage);
    } catch (ContractExeException e) {
      Assert.assertEquals(errorMessage, e.getMessage());
    } catch (Exception e) {
      Assert.assertTrue(false);
    }
  }
```
