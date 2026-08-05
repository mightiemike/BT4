This confirms the mechanism: `matchOrder()` in `MarketSellAssetActuator.java` throws `ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM)` when the number of maker orders matched at the best price(s) exceeds the hardcoded `MAX_MATCH_NUM = 20`, and this exception propagates out of `execute()` as a `ContractExeException`, reverting the whole transaction. [1](#0-0) 

### Title
Permissionless order-book fragmentation can permanently DoS TRC10 Market matching via `MAX_MATCH_NUM` - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
The TRC10 decentralized exchange ("Market") feature in java-tron lets any account place `MarketSellAssetContract` orders that are matched against the resting order book for a token pair. The matching loop in `matchOrder()` enforces a hard cap `MAX_MATCH_NUM = 20` on the number of maker orders it will consume in one taker transaction; exceeding it throws `ContractValidateException("Too many matches...")`, which aborts the whole transaction. [2](#0-1) [3](#0-2) 

An unprivileged actor can permissionlessly place many tiny resting orders (only bounded by their own `MAX_ACTIVE_ORDER_NUM = 100` per-account cap, which is trivially bypassed with multiple accounts) at the best price(s) for a token pair. [4](#0-3)  Any legitimate taker whose sell order would need to walk through more than 20 of these maker orders at a price level to fill will have `matchOrderCount` exceed `MAX_MATCH_NUM`, causing the entire `execute()` to fail and be converted into a `ContractExeException`, reverting the whole trade. [5](#0-4) 

### Finding Description
`MarketSellAssetActuator.matchOrder()` walks the resident maker orders for the opposing pair/price using `pairPriceToOrderStore` and `MarketOrderIdListCapsule`, incrementing `matchOrderCount` for each maker order consumed, and throws once the count exceeds `MAX_MATCH_NUM` (20): [6](#0-5) 

Order creation itself is fully permissionless — any account with the requisite TRX/TRC10 balance can call `MarketSellAssetContract`, and the only quantitative gate is `MAX_ACTIVE_ORDER_NUM = 100` *active orders per account*, not per pair/price level: [4](#0-3)  An attacker can use many funded accounts, each opening near-minimum-value orders at the most attractive price for a targeted token pair, to build up an arbitrarily deep queue of resting orders at that price bucket. Because the maker-side traversal in `matchOrder()` counts matched orders (not their value), any real trader whose order would otherwise clear through more than 20 of these attacker orders will always trigger the `"Too many matches"` `ContractValidateException`, which is caught in `execute()` and rethrown as a fatal `ContractExeException`, aborting the transaction entirely — the legitimate trader's balance changes are rolled back and they cannot trade at that price at all. [1](#0-0) 

This mirrors the root cause of the referenced report: a fixed, shared, per-key resource slot (there: 5 `BribeRewarder`s per pool/period; here: 20 matched maker orders per taker transaction) is filled unpermissioned with worthless/dust entries, permanently obstructing legitimate use of that resource by other, unrelated users.

### Impact Explanation
A malicious actor can effectively censor/DoS trading on any specific TRC10 token pair's best price level by littering it with dust maker orders spread across many accounts, at negligible cost (order creation fee is `getMarketSellFee()`, a small fixed TRX fee, and 1-unit token quantities). Legitimate sellers attempting to sell into that price bucket will have their transactions fail (energy/bandwidth is still consumed and the transaction is recorded as failed), and the exchange functionality for that pair becomes unusable at that price. This is a concrete availability/DoS impact on core exchange settlement logic, not merely theoretical.

### Likelihood Explanation
Likelihood is high: no privileged role is required, the fee is small and fixed, dust-quantity orders are cheap, and the attacker can distribute orders across arbitrarily many funded accounts to bypass the per-account `MAX_ACTIVE_ORDER_NUM` cap. The condition to trigger the DoS (more than 20 maker orders at/above the best matching price) is easy to construct deliberately and requires no timing precision.

### Recommendation
Change the matching behavior so that exceeding `MAX_MATCH_NUM` results in a partial fill (persisting the taker's remaining unmatched quantity back into the order book, as already done elsewhere via `saveRemainOrder()`) rather than aborting/reverting the whole transaction. Additionally, consider enforcing a minimum order quantity/value (analogous to a reward-token whitelist/minimum in the referenced report) or a per-price-level cap on the number of resident orders, so dust orders cannot cheaply monopolize a price bucket.

### Proof of Concept
1. Attacker controls N (e.g., 25) separate funded accounts.
2. For a target TRC10 pair (`sellTokenId`/`buyTokenId`), each account submits a `MarketSellAssetContract` with a minimal `sellTokenQuantity`/`buyTokenQuantity` at the most attractive price (e.g., price ratio 1:1), staying under each account's own `MAX_ACTIVE_ORDER_NUM` limit. [7](#0-6) 
3. This results in 25 resting maker orders at that single best price bucket.
4. A legitimate trader submits a normal-sized sell order for the opposing side that price-matches and attempts to walk through the order queue; `matchOrder()`'s `matchOrderCount` increments past `MAX_MATCH_NUM` (20) partway through the attacker's dust orders and throws `"Too many matches. MAX_MATCH_NUM = 20"`. [8](#0-7) 
5. `execute()` catches this `ContractValidateException` and converts it to `ContractExeException`, so the legitimate trader's transaction fails every time until enough of the dust orders are otherwise cleared by other means, effectively denying that trader (and anyone else similarly positioned) the ability to trade at that price. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L62-66)
```java
  @Getter
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
  @Getter
  private static int MAX_MATCH_NUM = 20;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L99-162)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    initStores();

    TransactionResultCapsule ret = (TransactionResultCapsule) object;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(TX_RESULT_NULL);
    }

    long fee = calcFee();

    try {
      final MarketSellAssetContract contract = this.any
          .unpack(MarketSellAssetContract.class);

      AccountCapsule accountCapsule = accountStore
          .get(contract.getOwnerAddress().toByteArray());

      sellTokenID = contract.getSellTokenId().toByteArray();
      buyTokenID = contract.getBuyTokenId().toByteArray();
      sellTokenQuantity = contract.getSellTokenQuantity();
      buyTokenQuantity = contract.getBuyTokenQuantity();
      MarketPrice takerPrice = MarketPrice.newBuilder()
          .setSellTokenQuantity(sellTokenQuantity)
          .setBuyTokenQuantity(buyTokenQuantity).build();

      // fee
      accountCapsule.setBalance(accountCapsule.getBalance() - fee);
      // add to blackhole address
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      // 1. transfer of balance
      transferBalanceOrToken(accountCapsule);

      // 2. create and save order
      MarketOrderCapsule orderCapsule = createAndSaveOrder(accountCapsule, contract);

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

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L232-239)
```java
    // check order num
    MarketAccountOrderCapsule marketAccountOrderCapsule = marketAccountStore
        .getUnchecked(ownerAddress);
    if (marketAccountOrderCapsule != null
        && marketAccountOrderCapsule.getCount() >= MAX_ACTIVE_ORDER_NUM) {
      throw new ContractValidateException(
          "Maximum number of orders exceeded，" + MAX_ACTIVE_ORDER_NUM);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L307-360)
```java
  private void matchOrder(MarketOrderCapsule takerCapsule, MarketPrice takerPrice,
      TransactionResultCapsule ret, AccountCapsule takerAccountCapsule)
      throws ItemNotFoundException, ContractValidateException {

    byte[] makerSellTokenID = buyTokenID;
    byte[] makerBuyTokenID = sellTokenID;
    byte[] makerPair = MarketUtils.createPairKey(makerSellTokenID, makerBuyTokenID);

    // makerPair not exists
    long makerPriceNumber = pairToPriceStore.getPriceNum(makerPair);
    if (makerPriceNumber == 0) {
      return;
    }
    long remainCount = makerPriceNumber;

    // get maker price list
    List<byte[]> priceKeysList = pairPriceToOrderStore
        .getPriceKeysList(MarketUtils.getPairPriceHeadKey(makerSellTokenID, makerBuyTokenID),
            (long) (MAX_MATCH_NUM + 1), makerPriceNumber, true);

    int matchOrderCount = 0;
    // match different price
    while (takerCapsule.getSellTokenQuantityRemain() != 0) {
      // get lowest ordersList
      MarketPrice makerPrice = hasMatch(priceKeysList, takerPrice);
      if (makerPrice == null) {
        return;
      }

      byte[] pairPriceKey = priceKeysList.get(0);

      // if not exists
      MarketOrderIdListCapsule orderIdListCapsule = pairPriceToOrderStore.get(pairPriceKey);

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
