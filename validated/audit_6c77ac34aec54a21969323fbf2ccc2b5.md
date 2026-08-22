### Title
Divergence between `MarketPairToPriceStore.getPriceNum` counter and actual live price-key count in `MarketPairPriceToOrderStore` after `MAX_MATCH_NUM` abort in `MarketSellAssetActuator.matchOrder` - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java)

### Summary
`MarketSellAssetActuator.matchOrder` throws `ContractValidateException("Too many matches...")` as soon as `matchOrderCount` exceeds `MAX_MATCH_NUM`, which can happen immediately after `orderIdListCapsule.removeOrder(...)` has already emptied and persisted a price bucket. The count-maintenance block that deletes the bucket from `pairPriceToOrderStore` and decrements/deletes the counter in `pairToPriceStore` sits *after* this throw point and is skipped, while the order-list mutation performed inside `removeOrder` is already written to the store.

### Finding Description
In `matchOrder` [1](#0-0) , the inner loop calls `matchSingleOrder` and, when a maker order is fully consumed, calls `orderIdListCapsule.removeOrder(...)`. `removeOrder` directly persists the updated (possibly now-empty) `MarketOrderIdListCapsule` to `pairPriceToOrderStore` via `pairPriceToOrderStore.put(pairPriceKey, this)` whenever the head/tail changes [2](#0-1) . Immediately afterward, `matchOrderCount++` is checked against `MAX_MATCH_NUM`, and if exceeded, a `ContractValidateException` is thrown right there [3](#0-2) .

The code block responsible for keeping the counter and the underlying key set consistent — deleting the emptied `pairPriceKey` from `pairPriceToOrderStore`, decrementing `remainCount`, and calling `pairToPriceStore.setPriceNum` / `pairToPriceStore.delete` — only executes *after* the inner while loop exits naturally [4](#0-3) . If the exception fires exactly when the last order at a price bucket was removed (i.e., `matchOrderCount` crosses `MAX_MATCH_NUM` on the same iteration that emptied `orderIdListCapsule`), this cleanup block is skipped entirely.

Because `execute()` only wraps the call in a broad `catch (... | ContractValidateException e)` that marks the receipt `FAILED` and rethrows `ContractExeException` [5](#0-4) , there is no per-actuator rollback of the store writes already performed by `removeOrder` prior to the exception — only the enclosing block/session mechanism reverts state, and that only applies when an *entire block* is invalid, not for a single failed transaction inside an otherwise valid block. The net effect: `pairPriceToOrderStore` is left with an emptied-but-undeleted entry for that price bucket, while `pairToPriceStore.getPriceNum` for the pair still reports the pre-transaction count, one higher than the number of buckets that actually contain live orders.

`GetMarketOrderListByPairServlet` and `Wallet.getMarketOrderListByPair` / `Wallet.getMarketPriceByPair` rely on `getPriceNum` to bound iteration over `getPriceKeysList`, so this divergence is externally observable: querying the pair afterward returns an order/price list whose length does not match the counter, breaking the invariant used for conservation/accounting audits [6](#0-5) [7](#0-6) .

### Impact Explanation
This is a state/accounting-integrity bug: the market order-book counter (`MarketPairToPriceStore`) can diverge from the actual live key/order structure (`MarketPairPriceToOrderStore`), corrupting downstream reads served by `GetMarketOrderListByPairServlet` / `Wallet.getMarketOrderListByPair`. It does not directly steal funds, but it breaks a documented invariant relied upon for market-state consistency and could cause subsequent `matchOrder` calls to mis-size `getPriceKeysList` requests (using a stale `makerPriceNumber`), potentially causing further match-order corruption or DoS on subsequent trades against that pair.

### Likelihood Explanation
Reachable by any unprivileged, funded account. An attacker (or two colluding attacker-controlled accounts) creates more than `MAX_MATCH_NUM` (20) small maker orders at the same price for a token pair (paying only the market fee per order), then submits a single taker `MarketSellAssetContract` that matches against all of them in one transaction, exhausting exactly the maker orders in that bucket on the 21st match. This requires no special privileges, only normal `MarketSellAssetContract` broadcasts, and is fully repeatable against any newly created pair.

### Recommendation
Move the `pairToPriceStore` count/key cleanup logic so it also executes (or is retried) when the loop exits due to the `MAX_MATCH_NUM` exception, or restructure `matchOrder` to check `matchOrderCount > MAX_MATCH_NUM` before performing `removeOrder`'s store-mutating side effects, ensuring the "orderIdListCapsule now empty ⇒ delete bucket ⇒ decrement/delete counter" sequence is atomic with the removal that made the bucket empty.

### Proof of Concept
JUnit-style outline (fits existing `MarketSellAssetActuatorTest` patterns):
1. Set `MarketSellAssetActuator.setMAX_MATCH_NUM` to a small value (e.g., 2) for test speed.
2. Seed `pairPriceToOrderStore` / `pairToPriceStore` with exactly `MAX_MATCH_NUM + 1` maker orders at the same price for pair (A,B), so `getPriceNum(A,B)` equals the number of price buckets (here 1, with multiple orders in one bucket, or arrange multiple buckets so that the (MAX_MATCH_NUM+1)-th match empties the last bucket).
3. Broadcast a taker `MarketSellAssetContract` sized to consume all maker orders across `MAX_MATCH_NUM + 1` matches so the exception fires exactly after `removeOrder` empties the final bucket.
4. Call `actuator.execute(ret)`, catch the expected `ContractExeException`.
5. Assert:
   - `pairToPriceStore.getPriceNum(sellTokenId, buyTokenId)` still returns the pre-transaction count (not decremented/deleted).
   - `pairPriceToOrderStore.has(pairPriceKey)` for the emptied bucket is still `true` (or contains an empty `MarketOrderIdListCapsule`), i.e., not deleted.
   - `Wallet.getMarketOrderListByPair` / `Wallet.getMarketPriceByPair` return a price/order list whose size does not equal `getPriceNum`. [8](#0-7)

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L307-380)
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

      // the orders of makerPrice have been all consumed
      if (orderIdListCapsule.isOrderEmpty()) {
        pairPriceToOrderStore.delete(pairPriceKey);

        // need to delete marketPair if no more price(priceKeysList is empty after deleting)
        priceKeysList.remove(0);

        // update priceInfo's count
        remainCount = remainCount - 1;
        // if really empty, need to delete token pair from pairToPriceStore
        if (remainCount == 0) {
          pairToPriceStore.delete(makerPair);
          break;
        } else {
          pairToPriceStore.setPriceNum(makerPair, remainCount);
        }
      }
    } // end while
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/MarketOrderIdListCapsule.java (L94-106)
```java
    } else {
      // current is head
      // head = current.next
      if (nextCapsule != null) {
        this.setHead(currentCapsule.getNext());
      } else {
        // need to delete, outside
        this.setHead(new byte[0]);
      }

      // head changed
      pairPriceToOrderStore.put(pairPriceKey, this);
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java (L32-43)
```java
  public long getPriceNum(byte[] key) {
    BytesCapsule bytesCapsule = get(key);
    if (bytesCapsule != null) {
      return ByteArray.toLong(bytesCapsule.getData());
    } else {
      return 0L;
    }
  }

  public long getPriceNum(byte[] sellTokenId, byte[] buyTokenId) {
    return getPriceNum(MarketUtils.createPairKey(sellTokenId, buyTokenId));
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java (L50-64)
```java
  public List<byte[]> getPriceKeysList(byte[] headKey, long count, long totalCount, boolean skip) {
    List<byte[]> result = new ArrayList<>();

    if (has(headKey)) {
      long limit = count > totalCount ? totalCount : count;
      if (skip) {
        // need to get one more
        result = getKeysNext(headKey, limit + 1).subList(1, (int)(limit + 1));
      } else {
        result = getKeysNext(headKey, limit);
      }
    }

    return result;
  }
```
