I have sufficient evidence to confirm the analog now.

### Title
Missing minimum order-amount check in `MarketSellAssetActuator` allows permanent storage bloat via dust orders - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
The `MarketSellAssetActuator.validate()` only rejects orders whose `sellTokenQuantity`/`buyTokenQuantity` are `<= 0` or above an upper `marketQuantityLimit`, but enforces no minimum economically meaningful order size. Combined with the fact that filled/matched orders are never deleted from `MarketOrderStore` (only marked `INACTIVE`/`CANCELED`), an attacker can pay a small, fixed `MarketSellFee` to permanently persist arbitrarily many 1-sun (or otherwise dust-sized) `MarketOrder` records, `MarketPairToPriceStore` price entries, and `MarketPairPriceToOrderStore` linked-list entries into chain state, analogous to the Acala `deposit_dex_share` finding where no minimum deposit allowed dust liquidity positions to bloat runtime storage.

### Finding Description
In `validate()`, the only quantity checks are: [1](#0-0) 
There is no floor/minimum value requirement — `sellTokenQuantity = 1` and `buyTokenQuantity = 1` pass validation as long as the account has enough balance to cover them plus the fee.

Each successful call to `execute()` calls `createAndSaveOrder`, which always writes a new `MarketOrderCapsule` keyed by an incrementing `total_count`-derived order id into `MarketOrderStore`, and updates the account's `MarketAccountOrderCapsule`: [2](#0-1) 

If the order is not fully matched, it is additionally linked into `MarketPairToPriceStore`/`MarketPairPriceToOrderStore` via `saveRemainOrder`: [3](#0-2) 

Crucially, whether an order is fully matched (`matchOrder`, setting state `INACTIVE`) or explicitly canceled (`MarketCancelOrderActuator`), the `MarketOrderCapsule` record itself is **never removed from `MarketOrderStore`** — only the pair/price index entries (`pairPriceToOrderStore`, `pairToPriceStore`) are cleaned up when a price bucket becomes empty: [4](#0-3) 

The only bound on concurrent orders is `MAX_ACTIVE_ORDER_NUM = 100` *active* orders per account: [5](#0-4) [6](#0-5) 
but once an order transitions out of `ACTIVE` state (matched or canceled) it frees a slot in that 100-order counter while its `MarketOrderCapsule` entry remains permanently in the store. This means a single account can cycle through the 100-order limit indefinitely, and any number of accounts can do so in parallel, each paying only `dynamicStore.getMarketSellFee()` (plus a cancel fee if canceling): [7](#0-6) 

### Impact Explanation
This allows unbounded, permanent growth of `MarketOrderStore` (and transient growth of `MarketPairToPriceStore`/`MarketPairPriceToOrderStore` for distinct dust price ratios) at negligible per-unit cost, since order value is not tied to fee or storage cost. Over time this increases full-node state size, sync time, and I/O/maintenance costs for all java-tron nodes — a state-bloat/underpriced-public-work impact matching the reported bug class, though it is a chain-wide storage-growth issue rather than a funds-loss or consensus-halting bug.

### Likelihood Explanation
The attack requires only a funded account and the market-transaction feature to be enabled via committee proposal (`dynamicStore.supportAllowMarketTransaction()`). No privileged role is needed, and the cost per persisted record is a fixed, low `MarketSellFee`, making the attack economically feasible for a moderately funded actor to run continuously across many accounts.

### Recommendation
Introduce a minimum `sellTokenQuantity`/`buyTokenQuantity` (or minimum notional value) requirement in `MarketSellAssetActuator.validate()`, and/or prune fully-inactive/canceled `MarketOrderCapsule` entries from `MarketOrderStore` after some retention period so dust orders do not accumulate indefinitely.

### Proof of Concept
1. Enable market transactions via committee proposal (`supportAllowMarketTransaction`).
2. Repeatedly submit `MarketSellAssetContract` transactions with `sellTokenQuantity = 1`, `buyTokenQuantity = 1` for TRX vs. some asset ID, from many funded accounts.
3. Each call passes `validate()` since only the `<= 0` and upper `quantityLimit` checks apply (`MarketSellAssetActuator.java:223-230`), and `createAndSaveOrder` writes a new permanent entry to `MarketOrderStore` for each call (`MarketSellAssetActuator.java:501-525`).
4. Cancel or let each order fully match to free the 100-active-order slot (`MarketCancelOrderActuator.java:104-138`), then repeat — the underlying `MarketOrderCapsule` records are never deleted, so `MarketOrderStore` grows without bound while the cost per order remains the fixed `MarketSellFee`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L63-64)
```java
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L288-291)
```java
  @Override
  public long calcFee() {
    return dynamicStore.getMarketSellFee();
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L104-138)
```java
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

      if (orderIdListCapsule.isOrderEmpty()) {
        // if orderList is empty, delete
        pairPriceToOrderStore.delete(pairPriceKey);

        // 3. modify priceList
        // decrease price number
        // if empty, delete token pair
        byte[] makerPair = MarketUtils
            .createPairKey(orderCapsule.getSellTokenId(), orderCapsule.getBuyTokenId());
        long remainCount = pairToPriceStore.getPriceNum(makerPair) - 1;
        if (remainCount == 0) {
          pairToPriceStore.delete(makerPair);
        } else {
          pairToPriceStore.setPriceNum(makerPair, remainCount);
        }
      }
```
