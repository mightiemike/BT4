### Title
Unbounded Persistent State Growth via Cheap Market Sell/Cancel Order Cycling - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java, actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java)

### Summary
The Mintbase report describes a class of bug where a cheap, unprivileged, repeatable call (`nft_on_approve`) permanently grows contract storage while the fee charged to the caller is fixed and does not scale with the storage it consumes, letting an attacker cheaply bloat state and exhaust the marketplace's storage funds. The java-tron analog is the on-chain market order feature: `MarketSellAssetActuator` and `MarketCancelOrderActuator` charge only fixed, committee-configurable fees (`getMarketSellFee()` / `getMarketCancelFee()`) that are unrelated to the amount of permanent storage created, and cancelled orders are never actually removed from the per-account order index or the global order store — only marked `CANCELED`. This lets any account repeatedly create and cancel orders to grow global, permanent chain state cheaply and without bound.

### Finding Description
`MarketSellAssetActuator.execute()` creates a new `MarketOrderCapsule` and persists it into `MarketOrderStore`, and appends the new order id to the caller's `MarketAccountOrderCapsule.ordersList` via `createAndSaveOrder()`: [1](#0-0) 

The only fee charged is a fixed, protocol-wide value returned by `calcFee()`, unrelated to the size or growth of the created records: [2](#0-1) 

Validation only bounds the number of *active* orders per account (`MAX_ACTIVE_ORDER_NUM = 100`, tracked via `getCount()`), not the total number of orders ever created (`getTotalCount()`), which is unbounded: [3](#0-2) 

When an order is cancelled, `MarketCancelOrderActuator.execute()` marks the order `CANCELED` and removes it only from the maker's `MarketOrderIdListCapsule` price-matching index, but never removes the order id from the account's `MarketAccountOrderCapsule.ordersList`, nor does it delete the `MarketOrderCapsule` entry from `MarketOrderStore`: [4](#0-3) 

`MarketAccountOrderCapsule` exposes an `addOrders()` that only appends, and while a `removeOrder()` method exists on this class, it is not invoked by the cancel actuator — the actuator only calls `MarketUtils.updateOrderState(...)`, which updates order state/count bookkeeping but not the underlying list of order ids: [5](#0-4) 

Test evidence confirms this: after cancelling one of five orders, the account's `count` drops to 4 (active) while `totalCount` remains 5 and the cancelled order's id is still stored in `ordersList` and in `MarketOrderStore` (now marked `CANCELED` rather than deleted): [6](#0-5) [7](#0-6) 

Because the 100-order cap only limits *active* orders, an attacker can repeatedly issue `MarketSellAssetContract` (paying only `getMarketSellFee()`) followed by `MarketCancelOrderContract` (paying only `getMarketCancelFee()`) in a loop, from a single funded account, permanently growing:
1. `MarketOrderStore` — one full `MarketOrderCapsule` record per cycle, never deleted.
2. `MarketAccountOrderCapsule.ordersList` for that account — grows by one entry per cycle, never trimmed.

Neither fee is a function of the record size or of the fact that the growth is permanent (unlike the ephemeral bandwidth/energy consumed by normal transactions), so the cost of growing global validator/full-node storage indefinitely is fixed and cheap, mirroring the "storage fee not collected" root cause in the Mintbase report.

### Impact Explanation
This allows an unprivileged, funded account to permanently and unboundedly grow on-chain state (`MarketOrderStore` and per-account `MarketAccountOrderCapsule`) at a flat, low cost per entry, independent of the actual storage footprint being created. Over time this can bloat every full node's/witness's local database (a store keyed by market order id that never shrinks), increasing disk/I/O costs, slowing down `MarketOrderStore` and `MarketAccountStore` iteration/queries, and increasing sync time for new nodes — a state-growth/resource-exhaustion DoS vector reachable purely through ordinary broadcast transactions (`MarketSellAssetContract` + `MarketCancelOrderContract`), no privileged role required.

### Likelihood Explanation
Likelihood is moderate-to-high in principle: `MarketSellAssetContract`/`MarketCancelOrderContract` are ordinary, permissionless contract types processed by any node from broadcast transactions, gated only by `dynamicStore.supportAllowMarketTransaction()` being enabled by the committee and by the fixed sell/cancel fees, which are cheap relative to indefinite storage growth. The bound (`MAX_ACTIVE_ORDER_NUM = 100`) only limits concurrently active orders, not cumulative/cancelled ones, so the attack requires no coordination beyond looping sell+cancel transactions from one or a few funded accounts. Actual exploitability at scale depends on the currently configured `getMarketSellFee()`/`getMarketCancelFee()` values (not confirmed here) relative to disk-cost economics, which would need on-chain parameter inspection to fully quantify.

### Recommendation
- On order cancellation, actually remove the cancelled order id from the owning account's `MarketAccountOrderCapsule.ordersList` (or otherwise reclaim/rotate the list) and delete or archive the corresponding `MarketOrderCapsule` from `MarketOrderStore` instead of retaining it forever with a `CANCELED` state.
- If audit/history of past orders must be retained, cap the retained history size per account (analogous to the existing `MAX_ACTIVE_ORDER_NUM`) or charge a storage-scaled fee for permanently retained cancelled/filled orders.
- Consider tying `getMarketSellFee()`/`getMarketCancelFee()` (or a portion of it) to the actual persisted byte size of the created records so that permanent storage growth is economically bounded, similar to the "require approvals to fund potential storage usage" remediation suggested in the source report.

### Proof of Concept
1. Enable market transactions (`AllowMarketTransaction` proposal) and fund an account with enough TRX/assets to repeatedly pay `getMarketSellFee()` and `getMarketCancelFee()`.
2. Loop: broadcast a `MarketSellAssetContract` (creates a new `MarketOrderCapsule` in `MarketOrderStore` and appends to `MarketAccountOrderCapsule.ordersList`, see `MarketSellAssetActuator.createAndSaveOrder()`), then immediately broadcast a `MarketCancelOrderContract` for that same order id (marks it `CANCELED` in `MarketOrderStore` but leaves the record and the account's list entry in place, see `MarketCancelOrderActuator.execute()`).
3. Observe that `MarketAccountOrderCapsule.getTotalCount()` and the size of `ordersList` for the attacking account grow without bound across iterations, and `MarketOrderStore` accumulates one permanent (never-deleted) `CANCELED` record per iteration, as confirmed by the existing test assertions where `getCount()` (active) decreases on cancel but `getTotalCount()`/list length do not.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L86-126)
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

      if (orderIdListCapsule.isOrderEmpty()) {
        // if orderList is empty, delete
        pairPriceToOrderStore.delete(pairPriceKey);

```

**File:** chainbase/src/main/java/org/tron/core/capsule/MarketAccountOrderCapsule.java (L55-74)
```java
  public void addOrders(ByteString order) {
    this.accountOrder = this.accountOrder.toBuilder()
        .addOrders(order)
        .build();

  }

  public void removeOrder(ByteString orderId) {
    List<ByteString> orderList = Lists.newArrayList();
    orderList.addAll(this.getOrdersList());
    orderList.remove(orderId);

    this.accountOrder = this.accountOrder.toBuilder()
        .setCount(this.getCount() - 1)
        .clearOrders()
        .addAllOrders(orderList)
        .build();


  }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketCancelOrderActuatorTest.java (L449-476)
```java
    ByteString orderId = accountOrderCapsule.getOrdersList().get(2);

    // cancel the third order
    cancelOrder(orderId);

    //check fee
    accountCapsule = accountStore
        .get(ByteArray.fromHexString(OWNER_ADDRESS_FIRST));

    Assert.assertEquals(balanceBefore,
        dbManager.getDynamicPropertiesStore().getMarketCancelFee() + accountCapsule.getBalance());

    //check token number return
    Assert.assertEquals(100L,accountCapsule.getAssetV2MapForTest().get(TOKEN_ID_ONE).longValue());

    //check accountOrder
    accountOrderCapsule = marketAccountStore.get(ByteArray.fromHexString(OWNER_ADDRESS_FIRST));
    Assert.assertEquals(4, accountOrderCapsule.getCount());
    Assert.assertEquals(5, accountOrderCapsule.getTotalCount());
    // orderId = accountOrderCapsule.getOrdersList().get(2);

    //check order
    MarketOrderCapsule orderCapsule = orderStore.get(orderId.toByteArray());
    Assert.assertEquals(0L, orderCapsule.getSellTokenQuantityRemain());
    Assert.assertEquals(100L, orderCapsule.getSellTokenQuantity());
    Assert.assertEquals(300L, orderCapsule.getBuyTokenQuantity());
    Assert.assertEquals(State.CANCELED, orderCapsule.getSt());

```

**File:** framework/src/test/java/org/tron/core/actuator/MarketCancelOrderActuatorTest.java (L726-736)
```java
    accountOrderCapsule = marketAccountStore.get(ByteArray.fromHexString(OWNER_ADDRESS_FIRST));
    Assert.assertEquals(0, accountOrderCapsule.getCount());
    Assert.assertEquals(1, accountOrderCapsule.getTotalCount());
    // orderId = accountOrderCapsule.getOrdersList().get(0);

    //check order
    MarketOrderCapsule orderCapsule = orderStore.get(orderId.toByteArray());
    Assert.assertEquals(0L, orderCapsule.getSellTokenQuantityRemain());
    Assert.assertEquals(100L, orderCapsule.getSellTokenQuantity());
    Assert.assertEquals(300L, orderCapsule.getBuyTokenQuantity());
    Assert.assertEquals(State.CANCELED, orderCapsule.getSt());
```
