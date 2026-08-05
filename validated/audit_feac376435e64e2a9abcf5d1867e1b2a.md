### Title
Unbounded per-account order list causes O(N) cost in `MarketUtils.updateOrderState` for a flat fee - ([File: chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java])

### Summary
`MarketUtils.updateOrderState` calls `MarketAccountOrderCapsule.removeOrder`, which copies and rebuilds the account's *entire* order list and re-serializes it into `marketAccountStore`, regardless of the current fee paid. Because order creation only checks a per-price-point cap (`MAX_ACTIVE_ORDER_NUM = 100`) rather than a global cap on the account-wide order list, an attacker can inflate this list to size N across many distinct price points at flat per-order fees, then trigger one flat-fee cancel/fill transaction that forces O(N) work.

### Finding Description
`MarketUtils.updateOrderState` fetches the caller's `MarketAccountOrderCapsule` and calls `accountOrderCapsule.removeOrder(orderCapsule.getID())` whenever an order transitions to `INACTIVE`/`CANCELED`, then writes the capsule back via `marketAccountStore.put(...)`. [1](#0-0) 

`MarketAccountOrderCapsule.removeOrder` copies the *entire* `getOrdersList()` into an `ArrayList`, performs a linear `List.remove(Object)` scan, then rebuilds the whole protobuf via `clearOrders().addAllOrders(orderList)` — an O(N) operation over the full account order list, not just the affected order. [2](#0-1) 

This is reachable from two unprivileged, real-transaction entrypoints:
- `MarketCancelOrderActuator.execute`, which charges only the flat `calcFee()` (market cancel fee) before calling `MarketUtils.updateOrderState(orderCapsule, State.CANCELED, marketAccountStore)`. [3](#0-2) 
- `MarketSellAssetActuator.matchSingleOrder`, invoked from a taker sell transaction, which calls `MarketUtils.updateOrderState(..., State.INACTIVE, marketAccountStore)` for any order (taker or maker) that becomes fully filled, again gated only by the flat sell/order fee. [4](#0-3) 

Order creation (`createAndSaveOrder`) unconditionally appends to the account's order list via `marketAccountOrderCapsule.addOrders(orderCapsule.getID())` with no visible cap on the total size of this account-wide list. [5](#0-4) 

The only size guard found, `MAX_ACTIVE_ORDER_NUM = 100`, is declared in `MarketSellAssetActuator` alongside `MAX_MATCH_NUM = 20`. [6](#0-5) 
This constant is referenced only within `MarketSellAssetActuator.java` and its test file — I could not confirm within the available index whether it bounds the per-price-point `MarketOrderIdListCapsule` (the linked list at a single price for a token pair) or the account-wide `MarketAccountOrderCapsule` list. Given that `MarketAccountOrderCapsule.getOrdersList()` aggregates orders across all token pairs and all price points for an account, and `MarketOrderIdListCapsule` is keyed per pair+price, a per-price-point cap of 100 would not prevent an attacker from accumulating a much larger account-wide list by spreading orders across many distinct price points (varying `buyTokenQuantity`/`sellTokenQuantity` combinations) or token pairs — each individually staying under any single-price-point limit.

If this account-wide list is indeed uncapped, then the fee model (a flat per-order creation/cancel/sell fee, independent of N) is decoupled from the actual state-mutation cost, which grows linearly (list copy + linear removal + full re-serialization) with the number of prior orders under that account.

### Impact Explanation
An attacker who has accumulated N orders under their own account can pay only the flat `getMarketCancelFee()` or `getMarketSellFee()`-equivalent fee to trigger a single `updateOrderState` call whose `removeOrder` + `marketAccountStore.put` cost scales with N. This underprices state-mutation work relative to the fee paid, and can be used to degrade block-processing latency for a given transaction, a self-inflated but externally-triggerable public-cost amplification.

### Likelihood Explanation
Feasibility depends entirely on whether any effective cap exists on the *account-wide* `MarketAccountOrderCapsule` order list size. The only cap I could confirm in the code (`MAX_ACTIVE_ORDER_NUM = 100`) is defined in the actuator responsible for per-order creation/matching, but its exact enforcement scope (single price bucket vs. whole account) could not be verified from the available index. If it only bounds a single `MarketOrderIdListCapsule` (per pair+price), the attack is straightforwardly repeatable: create many orders at distinct prices (cheap, i.e. flat fee each), then cancel/fill one to trigger the O(N) `removeOrder`+`put`.

### Recommendation
- Verify and, if necessary, enforce a hard cap on the total number of orders in `MarketAccountOrderCapsule.getOrdersList()` per account (not just per price-point), rejecting new order creation once the cap is reached.
- Replace the copy-and-rebuild `removeOrder` implementation with a data structure/approach that supports O(1) or O(log N) removal (e.g., use a `Set`/indexed structure, or restructure order storage to avoid rewriting the entire account order list on every cancel/fill).
- Consider scaling the market cancel/sell fee with the size of the account's current order list to align cost with the work performed.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/actuator/MarketAccountOrderScalingTest.java
@Test
public void removeOrderCostScalesWithAccountOrderListSize() throws Exception {
  InitAsset();
  int[] sizes = {10, 1000, 20000};
  long[] latencies = new long[sizes.length];

  for (int i = 0; i < sizes.length; i++) {
    // create `sizes[i]` orders under OWNER_ADDRESS_FIRST at DISTINCT prices
    // (vary buyTokenQuantity so each stays under any single-price cap)
    for (int j = 0; j < sizes[i]; j++) {
      addOrder(TRX, 100L, TOKEN_ID_TWO, 200L + j, OWNER_ADDRESS_FIRST);
    }

    MarketAccountOrderCapsule accountOrderCapsule =
        marketAccountStore.get(ByteArray.fromHexString(OWNER_ADDRESS_FIRST));
    ByteString lastOrderId =
        accountOrderCapsule.getOrdersList().get(accountOrderCapsule.getOrdersList().size() - 1);

    long start = System.nanoTime();
    cancelOrder(lastOrderId); // flat MarketCancelFee only
    latencies[i] = System.nanoTime() - start;
  }

  // Assert latency grows super-linearly relative to fee paid (which is constant),
  // demonstrating disproportionate, self-inflated public cost per flat-fee tx.
  Assert.assertTrue(latencies[2] > latencies[1] * 10);
  Assert.assertTrue(latencies[1] > latencies[0] * 10);
}
```
Note: this PoC assumes no effective account-wide order-count cap exists; if `MAX_ACTIVE_ORDER_NUM` (or an equivalent check) does bound the aggregate `MarketAccountOrderCapsule` list across all price points, the attack is infeasible and this finding should be downgraded — this could not be conclusively verified from the indexed code alone. A background Devin session with full repo access should confirm where/whether `MAX_ACTIVE_ORDER_NUM` is enforced against the account-wide list before treating this as confirmed.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L251-262)
```java
  public static void updateOrderState(MarketOrderCapsule orderCapsule,
      State state, MarketAccountStore marketAccountStore) throws ItemNotFoundException {
    orderCapsule.setState(state);

    // remove from account order list
    if (state == State.INACTIVE || state == State.CANCELED) {
      MarketAccountOrderCapsule accountOrderCapsule = marketAccountStore
          .get(orderCapsule.getOwnerAddress().toByteArray());
      accountOrderCapsule.removeOrder(orderCapsule.getID());
      marketAccountStore.put(accountOrderCapsule.createDbKey(), accountOrderCapsule);
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/MarketAccountOrderCapsule.java (L62-72)
```java
  public void removeOrder(ByteString orderId) {
    List<ByteString> orderList = Lists.newArrayList();
    orderList.addAll(this.getOrdersList());
    orderList.remove(orderId);

    this.accountOrder = this.accountOrder.toBuilder()
        .setCount(this.getCount() - 1)
        .clearOrders()
        .addAllOrders(orderList)
        .build();

```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L96-109)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L62-66)
```java
  @Getter
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
  @Getter
  private static int MAX_MATCH_NUM = 20;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L436-449)
```java
      if (takerSellTokenLeft == 0) {
        MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);
      }
      MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
    } else if (takerBuyTokenQuantityRemain < makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker < maker
      // if the quantity of taker want to buy is smaller than the remain of maker want to sell,
      // consume the order of the taker

      takerBuyTokenQuantityReceive = takerBuyTokenQuantityRemain;
      makerBuyTokenQuantityReceive = takerOrderCapsule.getSellTokenQuantityRemain();

      takerOrderCapsule.setSellTokenQuantityRemain(0);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);
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
