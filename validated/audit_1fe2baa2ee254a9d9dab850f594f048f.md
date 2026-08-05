Confirmed: `MarketUtils.returnSellTokenRemain` sets `orderCapsule.setSellTokenQuantityRemain(0L)` on the in-memory `makerOrderCapsule` object, and `updateOrderState` sets `orderCapsule.setState(State.INACTIVE)` — but in the `matchSingleOrder` early-return branch, `orderStore.put(makerOrderCapsule...)` (which normally happens at the end of the function) is never reached, so none of these mutations are persisted for the maker's order.

### Title
Maker order state discarded when rounding makes `makerBuyTokenQuantityReceive == 0`, allowing repeated free token withdrawal - ([File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java])

### Summary
`MarketSellAssetActuator.matchSingleOrder` walks the order book similarly to the reported bucket-redeem loop: it consumes maker orders in price order and is expected to persist the maker's updated state after each match. In the "taker > maker" branch, when integer division causes `makerBuyTokenQuantityReceive` to be `0`, the code mutates the in-memory `makerOrderCapsule` (marks it `INACTIVE`, zeroes its remaining sell quantity, and returns leftover tokens to the maker's account) and then `return`s early — skipping the `orderStore.put(makerOrderCapsule...)` call that persists these changes to the database.

### Finding Description
In `matchSingleOrder` [1](#0-0) , the "taker > maker" branch computes `makerBuyTokenQuantityReceive` via `MarketUtils.multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity, ...)`. When this rounds down to `0`:

```
MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
if (makerBuyTokenQuantityReceive == 0) {
  makerOrderCapsule.setSellTokenQuantityReturn();
  returnSellTokenRemain(makerOrderCapsule);
  return;
}
``` [2](#0-1) 

`returnSellTokenRemain` credits the maker's account with the remaining sell-token quantity and sets `orderCapsule.setSellTokenQuantityRemain(0L)` in memory [3](#0-2) , and `updateOrderState` marks the in-memory capsule `INACTIVE` and removes it from the account's active-order list in `marketAccountStore` [4](#0-3) . However, the `orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule)` call that normally persists these order-state changes only executes on the non-early-return paths [5](#0-4) . Because the `return` occurs first, the `MarketOrderStore` record for this maker order retains its old state: `ACTIVE` and non-zero `sellTokenQuantityRemain`.

The developer comment claims "it would not happen here" based on an assumed price-ratio invariant, but `validate()` only requires `sellTokenQuantity > 0` and `buyTokenQuantity > 0` [6](#0-5)  — it does not enforce any relationship preventing a maker order with `sellTokenQuantity >> buyTokenQuantity` (e.g., sell 1000 A for 1 TRX). For such an order, when its remaining amount is consumed down to a small residual, `multiplyAndDivide(remain, buyQty, sellQty)` legitimately rounds to `0`, entering the unpersisted branch.

### Impact Explanation
The maker's order remains listed as `ACTIVE` in `MarketOrderStore` and still linked in the price/order list (`pairPriceToOrderStore`), with its original `sellTokenQuantityRemain`, even though the maker's account has already been credited with the returned token balance. This is an accounting divergence: the order can be matched again in a subsequent `MarketSellAssetContract` transaction against the same stale quantity, triggering `returnSellTokenRemain` (or a normal match) a second time and crediting the maker's account again for tokens it no longer backs — effectively minting tokens/TRX out of thin air for repeated matches against the same "ghost" order. This is a concrete underpriced/duplicated-settlement bug in the exchange/market actuator, an unprivileged-user-reachable path (anyone can place market orders).

### Likelihood Explanation
Reachable by any account submitting a `MarketSellAssetContract` transaction with sellTokenQuantity chosen to create a favorable price ratio (e.g., sell 1,000,000 units for 1 unit) as a maker order, then triggering a taker match that leaves a small residual — well within attacker control since order quantities and prices are fully user-specified and only bounded by `getMarketQuantityLimit()`. No privileged role or special condition is required.

### Recommendation
Move `orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule)` before the early `return` in the `makerBuyTokenQuantityReceive == 0` branch (or restructure the function so persistence always occurs regardless of the return path), mirroring the bucket module fix of ensuring the state-update call executes before any `break`/`return` that would otherwise skip it.

### Proof of Concept
1. Account M places a sell order: `sellTokenId = A`, `sellTokenQuantity = 1_000_000`, `buyTokenId = TRX`, `buyTokenQuantity = 1` (maker order, price: needs 1,000,000 A per 1 TRX).
2. Account T places a matching buy order as taker with a TRX amount that leaves the maker's remaining sell quantity at a small residual (e.g., such that `makerSellRemainQuantity` after the trigger is `< 1_000_000 / 1` per-unit threshold, causing `multiplyAndDivide(remain, 1, 1_000_000)` to equal `0`).
3. `matchSingleOrder` executes the "taker > maker" branch, computes `makerBuyTokenQuantityReceive == 0`, calls `returnSellTokenRemain` (crediting M's account with the residual A tokens) and `updateOrderState(INACTIVE)`, then returns without calling `orderStore.put` for M's order.
4. Inspect `MarketOrderStore` for M's order ID: it still shows `State.ACTIVE` and the pre-match `sellTokenQuantityRemain`, while M's account balance already reflects the returned tokens.
5. Submit another taker order matching M's still-ACTIVE stale order to trigger a second settlement/return against the same already-credited quantity, duplicating value to M's account.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L223-225)
```java
    if (sellTokenQuantity <= 0 || buyTokenQuantity <= 0) {
      throw new ContractValidateException("token quantity must greater than zero");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-483)
```java
    } else {
      // taker > maker
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();

      // if the quantity of taker want to buy is bigger than the remain of maker want to sell,
      // consume the order of maker
      // makerSellTokenQuantityRemain_A/makerBuyTokenQuantityCurrent_TRX =
      //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());

      MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
      if (makerBuyTokenQuantityReceive == 0) {
        // the quantity is too small, return the remain of sellToken to maker
        // it would not happen here
        // for the maker, when sellQuantity < buyQuantity, it will get at least one buyToken
        // even when sellRemain = 1.
        // so if sellQuantity=200，buyQuantity=100, when sellRemain=1, it needs to be satisfied
        // the following conditions:
        // makerOrderCapsule.getSellTokenQuantityRemain() - takerBuyTokenQuantityRemain = 1
        // 200 - 200/100 * X = 1 ===> X = 199/2，and this comports with the fact that X is integer.
        makerOrderCapsule.setSellTokenQuantityReturn();
        returnSellTokenRemain(makerOrderCapsule);
        return;
      } else {
        makerOrderCapsule.setSellTokenQuantityRemain(0);
        takerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
            takerOrderCapsule.getSellTokenQuantityRemain(), makerBuyTokenQuantityReceive));
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L485-486)
```java
    // save makerOrderCapsule
    orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule);
```

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

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L280-295)
```java
  public static void returnSellTokenRemain(MarketOrderCapsule orderCapsule,
      AccountCapsule accountCapsule,
      DynamicPropertiesStore dynamicStore,
      AssetIssueStore assetIssueStore) {
    byte[] sellTokenId = orderCapsule.getSellTokenId();
    long sellTokenQuantityRemain = orderCapsule.getSellTokenQuantityRemain();
    if (Arrays.equals(sellTokenId, "_".getBytes())) {
      accountCapsule.setBalance(addExact(
          accountCapsule.getBalance(), sellTokenQuantityRemain,
          dynamicStore.disableJavaLangMath()));
    } else {
      accountCapsule
          .addAssetAmountV2(sellTokenId, sellTokenQuantityRemain, dynamicStore, assetIssueStore);
    }
    orderCapsule.setSellTokenQuantityRemain(0L);
  }
```
