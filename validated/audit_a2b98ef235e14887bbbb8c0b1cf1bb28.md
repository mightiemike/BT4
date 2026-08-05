### Title
Denial of Service on Limit Order Matching via `matchOrderCount` Cap in `MarketSellAssetActuator.matchOrder` - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchOrder` walks the resting order book for a token pair and aborts the **entire** taker transaction with a `ContractValidateException` the moment it consumes more than `MAX_MATCH_NUM` (20) maker orders [1](#0-0) . Because the check counts the number of orders touched rather than the value/size matched, and there is no minimum order-size enforcement, an attacker can flood the front of a price level with many tiny resting orders so that any legitimate taker whose order would need to consume more than 20 of them has their whole trade reverted, denying order fulfillment for that market pair — the same "griefing via list traversal" impact described in the external report, just triggered through a hard match-count ceiling instead of an unbounded walk.

### Finding Description
`matchOrder` fetches up to `MAX_MATCH_NUM + 1` price levels and then iterates maker orders at the best price via a linked list (`MarketOrderIdListCapsule`, head/tail pointers) [2](#0-1) . Every time a maker order is consumed, `matchOrderCount` is incremented, and once it exceeds `MAX_MATCH_NUM = 20` the method throws `ContractValidateException("Too many matches...")` [3](#0-2) . This exception propagates out of `execute()` [4](#0-3) , causing the whole taker transaction to fail rather than partially filling and resting the remainder — unlike a normal order book where hitting a processing cap should leave the remaining quantity as a new resting order.

Order creation only requires `sellTokenQuantity > 0` and `buyTokenQuantity > 0`, bounded above by `quantityLimit`, with no minimum size requirement (`MarketSellAssetActuator.validate`, lines 223–230) [5](#0-4) . Each account may hold up to `MAX_ACTIVE_ORDER_NUM = 100` active orders [6](#0-5) , so a handful of accounts can place 20+ dust-sized maker orders at the best price(s) of a given pair. Any taker whose incoming order would need to consume more than the first 20 of them will always trigger the `MAX_MATCH_NUM` guard and revert, regardless of how much liquidity is actually resting behind those dust orders.

### Impact Explanation
This is a concrete denial-of-service on the on-chain exchange/market functionality: legitimate large or ordinary taker orders against a griefed pair are permanently unable to execute as long as the attacker maintains the dust orders at the front of the book, matching the report's core impact of "preventing legitimate ITM/matching orders from being fulfilled." Users still pay bandwidth for the reverted transaction while receiving no trade execution, and market makers/takers are effectively locked out of that trading pair.

### Likelihood Explanation
Any unprivileged account can create market orders via `MarketSellAssetContract`/`MarketBuyAssetContract`. The cost to grief is low: an attacker only needs to place slightly more than `MAX_MATCH_NUM` (20) tiny orders at/near the best price of a pair, well within the per-account `MAX_ACTIVE_ORDER_NUM` (100) limit, and can use multiple accounts to increase the count further. No special privileges, timing, or race conditions are required, making this readily reproducible by any market participant.

### Recommendation
Do not abort the entire transaction when `MAX_MATCH_NUM` is exceeded. Instead, stop matching gracefully at the cap and persist the taker's unmatched remainder as a new resting order (as already done elsewhere in `execute()` via `saveRemainOrder`), so partial fills always succeed. Additionally, consider enforcing a minimum order size (relative to token decimals/value) to raise the cost of placing large numbers of dust orders, and/or making the match cap adaptive to actual quantity matched rather than pure order count.

### Proof of Concept
1. Attacker creates 21 small `MarketSellAssetContract` orders for pair (A, B) at the best price, each with minimal `sellTokenQuantity`/`buyTokenQuantity` (only constrained to be `> 0`), spread across one or more accounts to respect `MAX_ACTIVE_ORDER_NUM`.
2. A legitimate user submits a `MarketBuyAssetContract`/`MarketSellAssetContract` order large enough that, if fully matched, it would need to walk through more than 20 resting orders on the opposite side.
3. `matchOrder` iterates the linked order list, and after `matchOrderCount` exceeds `MAX_MATCH_NUM = 20`, throws `ContractValidateException("Too many matches. MAX_MATCH_NUM = 20")` [3](#0-2) .
4. The exception propagates to `execute()`'s catch block, the transaction is marked `FAILED`, and no matching or partial fill occurs [7](#0-6) .
5. As long as the attacker keeps ≥21 dust orders resting at the front of the book, every such legitimate order against that pair fails identically, denying trade execution.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L64-66)
```java
  private static int MAX_ACTIVE_ORDER_NUM = 100;
  @Getter
  private static int MAX_MATCH_NUM = 20;
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L223-230)
```java
    if (sellTokenQuantity <= 0 || buyTokenQuantity <= 0) {
      throw new ContractValidateException("token quantity must greater than zero");
    }

    long quantityLimit = dynamicStore.getMarketQuantityLimit();
    if (sellTokenQuantity > quantityLimit || buyTokenQuantity > quantityLimit) {
      throw new ContractValidateException("token quantity must less than " + quantityLimit);
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
