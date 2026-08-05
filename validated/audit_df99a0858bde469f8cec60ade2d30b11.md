## Finding: `MARKET_CANCEL_FEE` changes retroactively apply to already-resting orders

### Title
Governance-driven `MARKET_CANCEL_FEE` changes are retroactively applied to resting market orders instead of the fee agreed at order-creation time - (File: `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java`)

### Summary
`MarketCancelOrderActuator` (an unprivileged, user-triggered actuator that cancels a resting TRC10/TRX market order) computes its cancellation fee by reading the *current* `MARKET_CANCEL_FEE` dynamic parameter at cancel time, rather than the fee that was in effect when the order was originally placed. This is structurally the same defect described in the external report: a value that a user implicitly "locked in" when creating a long-lived position (there, a loan; here, a resting market order) is instead re-evaluated using the latest fee whenever the position is later closed.

### Finding Description
A market order created via `MarketSellAssetActuator` can remain "active" in the order book indefinitely if it is only partially matched (`getSellTokenQuantityRemain() != 0`), being stored in `orderStore`/`pairPriceToOrderStore` until it is fully matched or explicitly cancelled: [1](#0-0) .

`MARKET_CANCEL_FEE` is a dynamic chain parameter that can be changed at any time via a committee proposal (`ProposalType.MARKET_CANCEL_FEE`), bounded only to `[0, 10_000_000_000]` (up to 10,000 TRX), and the new value is written to `DynamicPropertiesStore` immediately once the proposal is approved: [2](#0-1) [3](#0-2) .

When a user later cancels their still-active order, `MarketCancelOrderActuator.calcFee()` simply returns whatever `MARKET_CANCEL_FEE` is stored **at that moment**, not the fee that existed when the order was created: [4](#0-3) 

This fee is then deducted from the user's balance and burned/sent to the blackhole address during `execute()`: [5](#0-4) 

The `MarketOrderCapsule` struct itself never records the fee (or fee rate) that was in effect at order creation, so there is no way to charge the originally-agreed fee — mirroring exactly the recommendation in the external report ("record the current fee ... at the time of creation").

Notably, java-tron's own historical parameters (`ENERGY_FEE`/`TRANSACTION_FEE`) already implement the correct pattern for a similar problem — they append every fee change to a price *history* string (`ENERGY_PRICE_HISTORY` / `BANDWIDTH_PRICE_HISTORY`) so a specific point in time can be priced correctly: [6](#0-5) . `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` were not given this treatment, and unlike `ENERGY_FEE`/`TRANSACTION_FEE` (which are consumed atomically per-transaction), a market order's cancellation is deferred to an arbitrary future block, making the missing snapshot a real, exploitable temporal mismatch.

### Impact Explanation
Any account with a resting (partially or fully unmatched) market order is exposed to an unbounded, unagreed-upon fee increase decided unilaterally by the committee after order placement, up to 10,000 TRX per cancellation. Because the order may sit in the book indefinitely, users have no way to know in advance what it will cost to exit their position; the actual charge depends solely on the parameter value at the block the cancel transaction executes, not on anything the user consented to when placing the order. This is a direct accounting/settlement correctness bug: the protocol charges an amount the user never agreed to for an action (cancel) they initiated under a different, expected fee.

### Likelihood Explanation
This triggers under entirely ordinary conditions requiring no attacker privilege: any user places a market order that doesn't fully match, and the committee (a normal, expected governance flow, not an attack) raises `MARKET_CANCEL_FEE` before the user cancels. Given `MARKET_CANCEL_FEE` is explicitly designed to be adjustable and no code path preserves the original fee, this will occur for any order that outlives one fee-change proposal cycle.

### Recommendation
Record the `MARKET_CANCEL_FEE` (and/or `MARKET_SELL_FEE`) that was in effect at order-creation time inside the `MarketOrder`/`MarketOrderCapsule` structure, and have `MarketCancelOrderActuator.calcFee()` read that stored value instead of the live `dynamicStore.getMarketCancelFee()`. Alternatively, adopt the same price-history mechanism already used for `ENERGY_FEE`/`TRANSACTION_FEE` (`ENERGY_PRICE_HISTORY`/`BANDWIDTH_PRICE_HISTORY`) and look up the fee applicable at the order's creation timestamp.

### Proof of Concept
1. Committee sets `MARKET_CANCEL_FEE = 1 TRX` (or 0).
2. Alice submits `MarketSellAssetContract` via `MarketSellAssetActuator`; the sell quantity is only partially matched, so the remainder is saved as a resting order in `orderStore`/`pairPriceToOrderStore` (`saveRemainOrder`) — see [1](#0-0) .
3. Committee later passes a proposal raising `MARKET_CANCEL_FEE` to `10,000 TRX` (the maximum allowed by `ProposalUtil` validation): [2](#0-1) . This is persisted immediately via `ProposalService.process` → `saveMarketCancelFee`.
4. Alice submits `MarketCancelOrderContract` to cancel her still-resting order. `MarketCancelOrderActuator.calcFee()` returns the new `10,000 TRX` value, which is deducted from her account balance in `execute()`, even though she agreed only to the `1 TRX` fee when she created the order.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L143-147)
```java
      if (orderCapsule.getSellTokenQuantityRemain() != 0) {
        saveRemainOrder(orderCapsule);
      }

      orderStore.put(orderCapsule.getID().toByteArray(), orderCapsule);
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L384-396)
```java
      case MARKET_CANCEL_FEE: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1)) {
          throw new ContractValidateException("Bad chain parameter id [MARKET_CANCEL_FEE]");
        }
        if (!dynamicPropertiesStore.supportAllowMarketTransaction()) {
          throw new ContractValidateException(
              "Market Transaction is not activated, can not set Market Cancel Fee");
        }
        if (value < 0 || value > 10_000_000_000L) {
          throw new ContractValidateException(
              "Bad MARKET_CANCEL_FEE parameter value, valid range is [0,10_000_000_000L]");
        }
        break;
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

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L232-235)
```java
        case MARKET_CANCEL_FEE: {
          manager.getDynamicPropertiesStore().saveMarketCancelFee(entry.getValue());
          break;
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
