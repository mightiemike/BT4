### Title
Resting market order fees (`MARKET_CANCEL_FEE`/`MARKET_SELL_FEE`) are not locked at order-creation time and can change before withdrawal - (File: actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java)

### Summary
`MarketSellAssetActuator` and `MarketCancelOrderActuator` compute the fee to charge a user by reading the current value of `MARKET_SELL_FEE` / `MARKET_CANCEL_FEE` from `DynamicPropertiesStore` at execution time, rather than the fee that was in effect when the order was created and left resting in the order book. Since these two parameters can be updated at any time by committee proposal with no price-history mechanism (unlike `ENERGY_FEE`/`TRANSACTION_FEE`, which do maintain a history), any account holding a resting order is exposed to an unexpected fee change between order placement and cancellation — the same bug class described in the referenced report (fee applied at settlement/withdrawal time differs from the fee agreed to at order creation time).

### Finding Description
When a user submits a `MarketSellAssetContract`, `MarketSellAssetActuator.execute()` charges `calcFee()` which returns `dynamicStore.getMarketSellFee()`: [1](#0-0) 

The resulting `MarketOrderCapsule`/`MarketOrder` proto record stores the order's token IDs, quantities and `create_time`, but has no field for the fee that applied at creation: [2](#0-1) 

If the order is not immediately fully matched, it rests in the order book (`ACTIVE` state) indefinitely, controlled entirely by `MAX_ACTIVE_ORDER_NUM`/matching logic, with no expiry. When the owner later cancels it via `MarketCancelOrderContract`, `MarketCancelOrderActuator.execute()`/`validate()` charge `calcFee()`, which is `dynamicStore.getMarketCancelFee()` fetched live at cancel time — not the fee in effect when the order was created: [3](#0-2) [4](#0-3) 

Both `MARKET_SELL_FEE` and `MARKET_CANCEL_FEE` can be changed at any time via an on-chain committee proposal, processed by `ProposalService.process()`: [5](#0-4) 

Unlike `ENERGY_FEE` and `TRANSACTION_FEE`, which explicitly maintain a price-history string (`saveEnergyPriceHistory`/`saveBandwidthPriceHistory`) that is later used to reconstruct the fee applicable at a given historical timestamp via `Wallet.getEnergyFee(long timestamp)`: [6](#0-5) [7](#0-6) 

`MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` have no equivalent history/lock mechanism, so an order resting between creation and cancellation is exposed to fee changes decided after the user committed to the order, exactly the bug class in the referenced report ("fee `can` change without the consent of users").

### Impact Explanation
Any account that places a market sell order and does not get it matched immediately bears open-ended fee risk on cancellation: the committee can raise `MARKET_CANCEL_FEE` after the order was created, and the user pays the new, higher fee they never agreed to when placing the order — a direct, unexpected loss of TRX for ordinary, unprivileged market participants. This mirrors the confirmed and fixed Putty Finance issue where fees applied at withdrawal differed from those in effect at order creation.

### Likelihood Explanation
Reaching this state requires only two ordinary, permissionless actions: broadcasting a `MarketSellAssetContract` that does not fully match (trivial, e.g. price/quantity that finds no counterparty) and later broadcasting a `MarketCancelOrderContract`. The fee change itself requires a passed committee proposal, which is a normal governance event on java-tron (not an attacker action), so the vulnerable condition (fee mismatch between creation and cancellation) can occur during ordinary chain operation with no special privileges needed by the affected user.

### Recommendation
Store the applicable fee (or a fee identifier/history index) inside `MarketOrder` at creation time (similar to how `create_time` is stored), and charge that stored fee on cancellation instead of re-reading the live `DynamicPropertiesStore` value. Alternatively, extend `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` with the same price-history mechanism already used for `ENERGY_FEE`/`TRANSACTION_FEE` and use `order.getCreateTime()` to look up the fee that was in effect at order-creation time when computing the cancel fee.

### Proof of Concept
1. Committee approves a proposal setting `MARKET_CANCEL_FEE` to a low value (e.g. 0).
2. User Alice broadcasts `MarketSellAssetContract` selling TRX for a token at a price with no matching counter-order; the order rests `ACTIVE` in the order book (see `MarketSellAssetActuator.createAndSaveOrder`, `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java:501-525`).
3. Committee approves a new proposal raising `MARKET_CANCEL_FEE` substantially (`ProposalService.process`, `MARKET_CANCEL_FEE` case).
4. Alice broadcasts `MarketCancelOrderContract` to cancel her still-resting order.
5. `MarketCancelOrderActuator.calcFee()` returns the new, higher `dynamicStore.getMarketCancelFee()` value and deducts it from Alice's balance — a fee she never agreed to when the order was created, with no on-chain mechanism to recover the fee that was actually in effect at order-creation time.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L288-291)
```java
  @Override
  public long calcFee() {
    return dynamicStore.getMarketSellFee();
  }
```

**File:** protocol/src/main/protos/core/Tron.proto (L61-84)
```text
// market
message MarketOrder {
    bytes order_id = 1;
    bytes owner_address = 2;
    int64 create_time = 3;
    bytes sell_token_id = 4;
    int64 sell_token_quantity = 5;
    bytes buy_token_id = 6;
    int64 buy_token_quantity = 7; // min to receive
    int64 sell_token_quantity_remain = 9;
    // When state != ACTIVE and sell_token_quantity_return !=0,
    //it means that some sell tokens are returned to the account due to insufficient remaining amount
    int64 sell_token_quantity_return = 10;

    enum State {
      ACTIVE = 0;
      INACTIVE = 1;
      CANCELED = 2;
    }
    State state = 11;

    bytes prev = 12;
    bytes next = 13;
}
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L84-102)
```java
    long fee = calcFee();

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
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L226-229)
```java
  @Override
  public long calcFee() {
    return dynamicStore.getMarketCancelFee();
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L83-90)
```java
        case ENERGY_FEE: {
          manager.getDynamicPropertiesStore().saveEnergyFee(entry.getValue());
          // update energy price history
          manager.getDynamicPropertiesStore().saveEnergyPriceHistory(
              manager.getDynamicPropertiesStore().getEnergyPriceHistory()
                  + "," + proposalCapsule.getExpirationTime() + ":" + entry.getValue());
          break;
        }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L228-234)
```java
        case MARKET_SELL_FEE: {
          manager.getDynamicPropertiesStore().saveMarketSellFee(entry.getValue());
          break;
        }
        case MARKET_CANCEL_FEE: {
          manager.getDynamicPropertiesStore().saveMarketCancelFee(entry.getValue());
          break;
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L4480-4496)
```java
  // this function should be called after EnergyPriceHistoryLoader done
  public long getEnergyFee(long timestamp) {
    try {
      String energyPriceHistory =
          chainBaseManager.getDynamicPropertiesStore().getEnergyPriceHistory();
      long energyFee = parseEnergyFee(timestamp, energyPriceHistory);

      if (energyFee == -1) {
        energyFee = getEnergyFee();
      }

      return energyFee;
    } catch (Exception e) {
      logger.error("GetEnergyFee timestamp={} failed", timestamp, e);
      return getEnergyFee();
    }
  }
```
