### Title
Order-book griefing via cheap orders can permanently DoS trading through a price level - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java)

### Summary
`MarketSellAssetActuator` caps the number of maker orders a single taker order can match against via a hard-coded `MAX_MATCH_NUM = 20` [1](#0-0) . If this limit is exceeded during matching, the actuator throws a `ContractValidateException`, which is caught and re-thrown as `ContractExeException`, failing the entire transaction [2](#0-1) [3](#0-2) . This is directly analogous to the SIZE bug class: any unprivileged, anonymous account can populate an order-book price level with many low-cost orders, and any subsequent good-faith transaction attempting to trade at/through that level is unconditionally rejected once the 20-order threshold is crossed — regardless of how the honest order was structured.

### Finding Description
`matchOrder()` walks the maker order list at the best matching price and counts every filled maker order in `matchOrderCount`; once it exceeds `MAX_MATCH_NUM` (20), it throws:
```java
matchOrderCount++;
if (matchOrderCount > MAX_MATCH_NUM) {
  throw new ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM);
}
``` [4](#0-3) 

This exception propagates out of `execute()` and is converted into a `ContractExeException`, so the taker's transaction fails outright [5](#0-4) . There is no accounting or filtering that distinguishes "attacker-planted dust orders" from legitimate maker orders — any account can call `MarketSellAssetActuator` (via a broadcast `MarketSellAssetContract` transaction) and place small maker orders at the best price for a token pair, since the only constraints are `firstTokenBalance/secondTokenBalance > 0` type checks in the sibling `ExchangeCreateActuator`/order creation path and a maximum active-order-count check, not a minimum order size [6](#0-5) . As with the SIZE bug, since transaction execution in java-tron runs inside a revoking DB session that is rolled back on failure (`ISession`/`SnapshotManager.revoke()`), a failing taker transaction leaves no persistent side effects and costs the attacker nothing beyond the initial dust order creation, which can later be cancelled to fully recover funds via `MarketCancelOrderActuator` [7](#0-6) . Once an attacker seeds >20 tiny orders at the best price for a pair, every legitimate order that would need to sweep through that price level to fill is permanently rejected with "Too many matches", exactly mirroring the SIZE report's "1000 invalid bids DOS the auction" pattern, except here the mechanism is a hard match-count cap in the on-chain order-matching loop rather than a bid-decryption/commitment check.

### Impact Explanation
Any account can permanently deny other users the ability to trade at a specific price for a specific token pair on the java-tron decentralized exchange (`MarketSellAssetContract`/`MarketOrder` system) by seeding the order book with many small, cheap orders at that price. This is a protocol-level DoS of the on-chain exchange functionality reachable purely through broadcast transactions from an unprivileged account, no special permissions required. It does not cause fund loss to the victim (their transaction simply fails/reverts), but it can render the exchange for a given pair effectively unusable at competitive prices, forcing takers into worse prices or repeated failed transactions (wasting bandwidth/fee on the failed attempt).

### Likelihood Explanation
Moderate. Unlike the original SIZE finding — where invalid bids cost the attacker literally nothing except gas — here the attacker must lock some quantity of the token being sold in each dust order (`transferBalanceOrToken`) [8](#0-7) . However, that quantity can be minimal (as small as the smallest representable unit for a given token pair), the order can later be cancelled and funds returned, and only ~20 orders are needed to overflow `MAX_MATCH_NUM` for a single price level. This makes the attack cheap and repeatable across many price bands, though it requires the attacker to actively monitor/anticipate the price levels that legitimate large orders would use.

### Recommendation
- Rather than hard-failing the entire transaction when `MAX_MATCH_NUM` is exceeded, partially fill the taker order up to the matchable limit and leave the remainder in the order book (mirrors the report's suggestion of graceful degradation instead of outright rejection).
- Consider a minimum order size (analogous to SIZE's suggested `minimumBidQuote`) to raise the cost of seeding many maker orders at a single price.
- Consider making `MAX_MATCH_NUM` a dynamic/governance-tunable parameter, and/or prioritize matching against maker orders by size to reduce the chance that many dust orders block honest fills.

### Proof of Concept
1. Attacker calls `MarketSellAssetActuator` 21+ times to create small sell orders (e.g., quantity = 1) for token pair `(A, B)` at the best price, each order persisted via `createAndSaveOrder`/`saveRemainOrder` [9](#0-8) [10](#0-9) .
2. A legitimate user submits a `MarketSellAssetContract` transaction on the opposite side that would match through more than 20 of these orders at that price level.
3. `matchOrder()` increments `matchOrderCount` past `MAX_MATCH_NUM` and throws `"Too many matches. MAX_MATCH_NUM = 20"`, which is exactly reproduced by the existing test `exceedMaxMatchNumLimit` in the test suite [11](#0-10) , confirming the throw path is reachable and reliably triggered with as few as 21 pre-existing maker orders at a price.
4. The legitimate user's transaction fails entirely; the attacker's orders remain intact in the book (and can be cancelled at will), so the DoS can be repeated indefinitely at low cost.

Note: I was unable to fully verify (within the tool budget) whether any dynamic-property-gated minimum order size or per-pair order limit (beyond `MAX_ACTIVE_ORDER_NUM = 100`) further constrains this scenario in production releases; if `MAX_ACTIVE_ORDER_NUM` is a per-account or per-pair cap, that value (100) is still far above the 20 needed to trigger the DoS, so it does not mitigate the issue.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L62-66)
```java
  @Getter
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
  @Getter
  private static int MAX_MATCH_NUM = 20;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L99-99)
```java
  @Override
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L139-159)
```java
      // 3. match order
      matchOrder(orderCapsule, takerPrice, ret, accountCapsule);

      // 4. save remain order into order book
      if (orderCapsule.getSellTokenQuantityRemain() != 0) {
        saveRemainOrder(orderCapsule);
      }

      orderStore.put(orderCapsule.getID().toByteArray(), orderCapsule);
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      ret.setOrderId(orderCapsule.getID());
      ret.setStatus(fee, code.SUCESS);
    } catch (ItemNotFoundException
        | InvalidProtocolBufferException
        | BalanceInsufficientException
        | ContractValidateException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L342-360)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L501-525)
```java
  private MarketOrderCapsule createAndSaveOrder(AccountCapsule accountCapsule,
      MarketSellAssetContract contract) {
    MarketAccountOrderCapsule marketAccountOrderCapsule = marketAccountStore
        .getUnchecked(contract.getOwnerAddress().toByteArray());
    if (marketAccountOrderCapsule == null) {
      marketAccountOrderCapsule = new MarketAccountOrderCapsule(contract.getOwnerAddress());
    }

    // note: here use total_count
    byte[] orderId = MarketUtils
        .calculateOrderId(contract.getOwnerAddress(), sellTokenID, buyTokenID,
            marketAccountOrderCapsule.getTotalCount());
    MarketOrderCapsule orderCapsule = new MarketOrderCapsule(orderId, contract);

    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    orderCapsule.setCreateTime(now);

    marketAccountOrderCapsule.addOrders(orderCapsule.getID());
    marketAccountOrderCapsule.setCount(marketAccountOrderCapsule.getCount() + 1);
    marketAccountOrderCapsule.setTotalCount(marketAccountOrderCapsule.getTotalCount() + 1);
    marketAccountStore.put(accountCapsule.createDbKey(), marketAccountOrderCapsule);
    orderStore.put(orderId, orderCapsule);

    return orderCapsule;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L527-535)
```java
  private void transferBalanceOrToken(AccountCapsule accountCapsule) {
    if (Arrays.equals(sellTokenID, "_".getBytes())) {
      accountCapsule.setBalance(subtractExact(
          accountCapsule.getBalance(), sellTokenQuantity));
    } else {
      accountCapsule
          .reduceAssetAmountV2(sellTokenID, sellTokenQuantity, dynamicStore, assetIssueStore);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L572-594)
```java
  private void saveRemainOrder(MarketOrderCapsule orderCapsule)
      throws ItemNotFoundException {
    // add order into orderList
    byte[] pairPriceKey = MarketUtils.createPairPriceKey(
        sellTokenID,
        buyTokenID,
        sellTokenQuantity,
        buyTokenQuantity
    );

    MarketOrderIdListCapsule orderIdListCapsule = pairPriceToOrderStore.getUnchecked(pairPriceKey);
    if (orderIdListCapsule == null) {
      orderIdListCapsule = new MarketOrderIdListCapsule();

      // pairPriceKey not exists, increase price count:
      // if pair not exits, add token pair, set count = 1, add headKey to pairPriceToOrderStore
      // if pair exists, increase count
      pairToPriceStore.addNewPriceKey(sellTokenID, buyTokenID, pairPriceToOrderStore);
    }

    orderIdListCapsule.addOrder(orderCapsule, orderStore);
    pairPriceToOrderStore.put(pairPriceKey, orderIdListCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L86-121)
```java
    try {
      final MarketCancelOrderContract contract = this.any
          .unpack(MarketCancelOrderContract.class);

      AccountCapsule accountCapsule = accountStore
          .get(contract.getOwnerAddress().toByteArray());

      byte[] orderId = contract.getOrderId().toByteArray();
      MarketOrderCapsule orderCapsule = orderStore.get(orderId);

      // fee
      accountCapsule.setBalance(accountCapsule.getBalance() - fee);
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      // 1. return balance and token
      MarketUtils
          .returnSellTokenRemain(orderCapsule, accountCapsule, dynamicStore, assetIssueStore);

      MarketUtils.updateOrderState(orderCapsule, State.CANCELED, marketAccountStore);
      accountStore.put(orderCapsule.getOwnerAddress().toByteArray(), accountCapsule);
      orderStore.put(orderCapsule.getID().toByteArray(), orderCapsule);

      // 2. clear orderList
      byte[] pairPriceKey = MarketUtils.createPairPriceKey(
          orderCapsule.getSellTokenId(),
          orderCapsule.getBuyTokenId(),
          orderCapsule.getSellTokenQuantity(),
          orderCapsule.getBuyTokenQuantity()
      );
      MarketOrderIdListCapsule orderIdListCapsule = pairPriceToOrderStore.get(pairPriceKey);

      // delete order
      orderIdListCapsule.removeOrder(orderCapsule, orderStore, pairPriceKey, pairPriceToOrderStore);
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
