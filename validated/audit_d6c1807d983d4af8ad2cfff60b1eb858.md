## Title
DoS via dust maker orders exhausting `MAX_MATCH_NUM` in `MarketSellAssetActuator` matching loop - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
Java-tron's TRC10 order-book exchange (`MarketSellAssetContract` / `MarketSellAssetActuator`) has the same root cause as the reported CLOB issue: partial fills leave maker orders in the book with no minimum-size enforcement, and the matching loop counts every maker order it visits toward a hard cap (`MAX_MATCH_NUM = 20`). Filling the best price level with many tiny/dust orders lets an attacker force any subsequent taker transaction at that price to revert with `ContractValidateException("Too many matches...")`, blocking normal order execution against that pair.

### Finding Description
`MarketSellAssetActuator.validate()` only checks that `sellTokenQuantity > 0` and below `getMarketQuantityLimit()` — there is no minimum order size check [1](#0-0) .

In `matchSingleOrder`, when a taker's remaining amount is smaller than a maker's remaining amount ("taker < maker" branch), the maker order is only partially consumed and its remaining quantity can be reduced to an arbitrarily small (dust) value with no minimum-amount check before being left active in the book: [2](#0-1) .

The order is only removed from the price-level list when its remaining quantity is exactly zero: [3](#0-2) .

Separately (and more directly attacker-controlled), a user can also directly create many tiny maker orders (e.g. `sellTokenQuantity = 1`) at the same best price, since there is no per-price-level cap — only a per-account active-order cap of 100 (`MAX_ACTIVE_ORDER_NUM`), which multiple attacker-controlled accounts can each fill: [4](#0-3) .

The core `matchOrder` loop increments `matchOrderCount` once per maker order visited — regardless of whether that match transferred any meaningful value — and reverts the whole transaction once more than `MAX_MATCH_NUM` (20) orders are touched at the current price level: [5](#0-4) .

This `ContractValidateException` propagates out of `execute()` and is converted into a `ContractExeException`, failing the entire taker transaction: [6](#0-5) .

Thus, an attacker who populates the best price level of a token pair with more than 20 tiny orders (via dust remainders from repeated partial fills, or simply by broadcasting many minimal-size `MarketSellAssetContract` transactions from multiple accounts) can force any subsequent taker order matching at that price level to always hit the `MAX_MATCH_NUM` cap and revert, effectively blocking normal trading (order posting/matching) for that pair at that price — the same "dust orders block order posting" bug class as the original CLOB.sol report.

### Impact Explanation
This is a low-cost, permissionless griefing/DoS vector against the on-chain TRC10 exchange (`Market*` actuators), reachable purely by broadcasting standard `MarketSellAssetContract` transactions from unprivileged accounts. It does not compromise consensus or funds directly, but it can reliably deny legitimate users the ability to have their sell/buy orders matched against a targeted price level, since their transactions will revert with "Too many matches" every time they attempt to trade at that price.

### Likelihood Explanation
Likelihood is moderate-to-high: creating minimal-size orders only requires paying `getMarketSellFee()` per order and holding a tiny token/TRX balance, and no minimum order size or per-price-level order count is enforced. An attacker only needs to place 21 dust orders at the current best price of a targeted pair (spread across accounts to respect the 100-orders-per-account cap) to trigger the condition for any taker whose order would otherwise match into that price level.

### Recommendation
- Enforce a minimum remaining order size (analogous to `minLimitOrderAmountInBase`) both at order creation (`validate()`) and after partial fills in `matchSingleOrder`, removing/canceling any maker order whose remaining quantity falls below the minimum instead of leaving dust in the book.
- Decouple `MAX_MATCH_NUM` accounting from "orders visited" and instead bound it by meaningful work done, or increase resilience by skipping/removing dust orders without counting them toward the match limit, so dust orders cannot be used to reliably exhaust the match budget of legitimate takers.

### Proof of Concept
1. Attacker (using one or more accounts) broadcasts `MarketSellAssetContract` transactions to place 21+ minimal-size sell orders (e.g. `sellTokenQuantity = 1`) for pair `(TOKEN_A -> TRX)` at the best price, saved via `saveRemainOrder` into the same `pairPriceToOrderStore` price-level list [7](#0-6) .
2. A legitimate user broadcasts a `MarketSellAssetContract` (`TRX -> TOKEN_A`) intended to match against this price level.
3. `matchOrder` iterates the order list, calling `matchSingleOrder` for each of the 21+ dust orders and incrementing `matchOrderCount` each time [5](#0-4) .
4. Once `matchOrderCount > MAX_MATCH_NUM` (20), `ContractValidateException("Too many matches. MAX_MATCH_NUM = 20")` is thrown, the legitimate user's transaction fails, and the attacker can repeat this indefinitely by re-posting dust orders to keep blocking trading at that price level.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L62-66)
```java
  @Getter
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
  @Getter
  private static int MAX_MATCH_NUM = 20;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L152-159)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L440-452)
```java
    } else if (takerBuyTokenQuantityRemain < makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker < maker
      // if the quantity of taker want to buy is smaller than the remain of maker want to sell,
      // consume the order of the taker

      takerBuyTokenQuantityReceive = takerBuyTokenQuantityRemain;
      makerBuyTokenQuantityReceive = takerOrderCapsule.getSellTokenQuantityRemain();

      takerOrderCapsule.setSellTokenQuantityRemain(0);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);

      makerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
          makerOrderCapsule.getSellTokenQuantityRemain(), takerBuyTokenQuantityRemain));
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
