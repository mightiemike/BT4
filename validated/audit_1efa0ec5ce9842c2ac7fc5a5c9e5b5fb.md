### Title
Underpriced Permanent State Growth via Zero-Cost Market Order Creation/Cancellation - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java, actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java)

### Summary
The external report's bug class is: an unprivileged, externally callable function increases persistent storage usage while the protocol-level fee charged for that storage increase is governance-configurable and defaults to zero, letting an attacker cheaply grow permanent on-chain state. The closest reachable analog in java-tron is the TRC10/TRX market module: `MarketSellAssetActuator` (create order) and `MarketCancelOrderActuator` (cancel order) both charge a dedicated protocol fee — `MARKET_SELL_FEE` / `MARKET_CANCEL_FEE` — that is separate from the ordinary bandwidth/energy cost and whose chain-configured default value is `0`.

### Finding Description
Every call to `MarketSellAssetContract` writes to four separate persistent stores: `MarketOrderStore`, `MarketAccountStore` (`MarketAccountOrderCapsule`), `MarketPairToPriceStore`, and `MarketPairPriceToOrderStore`. [1](#0-0) 

The fee actually charged for this multi-store write is `calcFee()`, which simply returns `dynamicStore.getMarketSellFee()`: [2](#0-1) 

and the chain's default value for this parameter, set at genesis unless a committee proposal changes it, is `0L`: [3](#0-2) 

Symmetrically, cancelling an order (`MarketCancelOrderActuator`) removes only the *index* entries (`pairPriceToOrderStore` / `pairToPriceStore`) but leaves the `MarketOrderCapsule` itself permanently in `MarketOrderStore` with state `CANCELED` — it is never deleted: [4](#0-3) 

The cancel fee is likewise `dynamicStore.getMarketCancelFee()`, also defaulting to `0L`: [5](#0-4) [6](#0-5) 

`MAX_ACTIVE_ORDER_NUM = 100` bounds only the number of *simultaneously active* orders per account: [7](#0-6) [8](#0-7) 

but it does not bound the number of orders that have been *created-then-cancelled*, since cancelled `MarketOrderCapsule` records are never purged from `MarketOrderStore`. Because `sellTokenQuantity`/`buyTokenQuantity` need only be `> 0`, a caller can create and immediately cancel orders using a trivially small locked amount (as low as 1 sun/unit), repeating this cycle indefinitely with the same 100-order "active" window, permanently growing `MarketOrderStore` at a marginal protocol cost of `MARKET_SELL_FEE + MARKET_CANCEL_FEE == 0`. This mirrors the report's root cause exactly: an externally reachable function that increases persistent storage footprint while the storage-specific fee path is not enforced to a nonzero value by default, distinct from the generic bandwidth/energy cost that every transaction already pays (which covers execution, not accumulated state).

### Impact Explanation
This is an underpriced-public-work class issue: full nodes must permanently store every `MarketOrderCapsule` ever created (cancelled or not), plus per-account `MarketAccountOrderCapsule` growth, at zero additional protocol-defined cost beyond ordinary bandwidth. An attacker can use this to grow chain state (disk usage, sync time, iteration cost for any code that scans `MarketOrderStore`) cheaply and repeatedly, which is a state-bloat/resource-exhaustion concern for node operators — the direct java-tron analog of "locking up storage without paying storage fees" from the report. It does not directly lock other users' funds (unlike the Mintbase case, where the marketplace contract itself bears the storage cost), because in java-tron each order still requires the caller's own `sellTokenQuantity` to be moved/locked temporarily; the impact here is state-growth/DoS-adjacent rather than fund lockup.

### Likelihood Explanation
`MARKET_SELL_FEE` and `MARKET_CANCEL_FEE` are governance parameters that currently default to `0`, and `MarketSellAssetContract`/`MarketCancelOrderContract` are ordinary, fully unprivileged transaction types reachable by any funded account (funds needed are minimal — 1 sun of any tradable token). No special permission or trusted role is required, and the flow (create with quantity=1, cancel, repeat) is a matter of scripting bandwidth-paying transactions.

### Recommendation
Set nonzero default values for `MARKET_SELL_FEE` and `MARKET_CANCEL_FEE` proportional to the marginal storage cost of persisting order records, and/or actively prune/garbage-collect terminal-state (`CANCELED`/fully filled) `MarketOrderCapsule` entries from `MarketOrderStore` instead of retaining them indefinitely, so that permanent state growth is bounded by an economic cost rather than relying solely on a governance-adjustable fee whose default is zero.

### Proof of Concept
1. On a chain where `ALLOW_MARKET_TRANSACTION` is enabled and `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` remain at their default `0` values, fund an account with a minimal TRC10 token balance (e.g., 100 units) or TRX.
2. Submit `MarketSellAssetContract` with `sellTokenQuantity = 1`, `buyTokenQuantity = 1` (any distinct token pair) — this writes new entries into `MarketOrderStore`, `MarketAccountStore`, `MarketPairToPriceStore`, `MarketPairPriceToOrderStore` per `MarketSellAssetActuator.execute` (lines 133–148 above) while `calcFee()` charges `0`.
3. Submit `MarketCancelOrderContract` for that order — the index entries are removed but the `MarketOrderCapsule` remains permanently in `MarketOrderStore` with state `CANCELED` (lines 103–138 above), and `calcFee()` again charges `0`.
4. Repeat steps 2–3 in a loop (bounded only by the 100-active-order cap, which does not block repeated create/cancel cycles) to permanently grow `MarketOrderStore` at zero marginal protocol fee beyond standard bandwidth consumption.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L62-64)
```java
  @Getter
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L136-148)
```java
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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L568-578)
```java
    try {
      this.getMarketSellFee();
    } catch (IllegalArgumentException e) {
      this.saveMarketSellFee(0L); // 0L
    }

    try {
      this.getMarketCancelFee();
    } catch (IllegalArgumentException e) {
      this.saveMarketCancelFee(0L);
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1661-1672)
```java
  public void saveMarketSellFee(long fee) {
    this.put(MARKET_SELL_FEE,
        new BytesCapsule(ByteArray.fromLong(fee)));
  }

  public long getMarketSellFee() {
    return Optional.ofNullable(getUnchecked(MARKET_SELL_FEE))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElseThrow(
            () -> new IllegalArgumentException("not found MARKET_SELL_FEE"));
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L103-138)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L226-229)
```java
  @Override
  public long calcFee() {
    return dynamicStore.getMarketCancelFee();
  }
```
