### Title
DoS of large TRC10 market taker orders via order-book fragmentation (MAX_MATCH_NUM griefing) - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java)

### Summary
`MarketSellAssetActuator` implements java-tron's on-chain TRC10 order-book "market" (an unprivileged, permissionless DEX reachable by any broadcast `MarketSellAssetContract` transaction). Like the DSC `liquidate()` bug, a large "taker" trade is matched against a shared, third-party-controlled resource (the maker order book) whose state can be manipulated in advance by any unrelated address. If the number of maker orders needed to fully fill a taker order exceeds `MAX_MATCH_NUM` (20), the whole transaction throws and fails, exactly mirroring the "full operation blocked, forced into inefficient partial operations" pattern described in the report.

### Finding Description
`matchOrder()`/`matchSingleOrder()` walk the maker order book at the best price and match against the taker's remaining sell quantity one maker order at a time: [1](#0-0) 

Each iteration increments `matchOrderCount`, and if it exceeds the hardcoded limit `MAX_MATCH_NUM = 20`, the method throws `ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM)`: [2](#0-1) 

This exception is caught in `execute()` and rethrown as `ContractExeException`, which fails the whole transaction (fee is still consumed, and none of the trade partial-fills at that point are what the taker intended — the operation as a whole is DoS'd): [3](#0-2) 

Crucially, any unprivileged address can pre-populate the order book for a given token pair/price with many small orders (up to `MAX_ACTIVE_ORDER_NUM = 100` per account, and unlimited across many accounts), since placing a `MarketSellAssetContract` order is fully permissionless and requires no special relationship to the eventual taker: [4](#0-3) 

An attacker who anticipates (or observes in the mempool) a large incoming market-sell order can front-run it by splitting the best-price liquidity into more than 20 tiny maker orders at that price level. When the victim's large taker order is then processed, `matchOrder()` will iterate through more than `MAX_MATCH_NUM` maker orders trying to fill the taker's remaining quantity, hit the limit, and abort the entire transaction — even though sufficient liquidity/quantity existed to fill the order, exactly as in the original report where sufficient collateral/debt existed but the operation was still blocked by an attacker-induced condition.

This is analogous to the original bug class: a permissionless, third-party-controllable piece of shared state (order book granularity / `s_DSCMinted` balance) is manipulated by an unrelated address specifically to make an honest actor's exact, all-at-once operation revert, forcing them into repeated smaller/partial operations (partial fills / partial liquidations) which is explicitly called out as the harmful outcome in the original report.

### Impact Explanation
Any user attempting to execute a large TRC10 market sell/trade can be denied the ability to have their order fully match in a single transaction. The victim's transaction fails (fee consumed, `code.FAILED`), and they are forced to either split their own order into many smaller transactions (worse execution, more fees, additional slippage across multiple blocks) or repeatedly retry — degrading DEX usability and enabling deliberate griefing/DoS of specific counterparties or of the market in general. This does not directly corrupt account balances but constitutes a targeted denial-of-service on a core exchange/market feature of the protocol.

### Likelihood Explanation
Likelihood is moderate: creating >20 small maker orders at a specific price level costs the attacker only the `MarketSellFee` per order and requires holding a modest amount of the token being offered (or TRX) — it does not require holding any of the victim's assets. The attacker does not need privileged access; anyone can broadcast `MarketSellAssetContract` transactions. The main constraint is that the attacker needs to place the fragmented orders at exactly the best matching price before the victim's transaction is processed, which is feasible via mempool observation or by pre-positioning orders in an actively traded pair.

### Recommendation
- Instead of aborting the entire transaction when `MAX_MATCH_NUM` is exceeded, stop matching further and persist the partial fill achieved so far (return the unmatched remainder to the order book as a resting order), similar to how partial fills are already handled when liquidity runs out. This avoids consuming the fee and losing the entire trade due to book fragmentation.
- Alternatively/additionally, increase `MAX_MATCH_NUM` or make it a configurable dynamic property, and/or aggregate matches at the same price/maker in a way that reduces the number of iterations counted, so that reasonable order-splitting cannot easily force failure of a legitimately sized taker order.
- Consider charging matching cost proportional to actual iterations rather than an all-or-nothing revert, aligning behavior with the "graceful degradation" recommendation from the original report (i.e., do the maximum possible fill rather than reverting to zero).

### Proof of Concept
1. Attacker places ≥ `MAX_MATCH_NUM + 1` (i.e., 21+) small `MarketSellAssetContract` orders selling token B for token A at the best available price for the pair (A→B), each with a minimal `sellTokenQuantity`/`buyTokenQuantity` (this is exercised by the existing test `exceedMaxMatchNumLimit`, which sets up `limit + 1` orders and asserts the taker transaction fails with `"Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM`): [5](#0-4) 
2. Victim broadcasts a large `MarketSellAssetContract` selling token A for token B at a price that matches the fragmented book, expecting a full fill in one transaction.
3. `matchOrder()` iterates the 21+ maker orders, `matchOrderCount` exceeds `MAX_MATCH_NUM`, and throws `ContractValidateException("Too many matches...")`, which propagates as `ContractExeException` in `execute()`, causing the victim's transaction to fail entirely (fee consumed, order not filled), analogous to the original DSCEngine liquidation DoS.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L60-67)
```java
public class MarketSellAssetActuator extends AbstractActuator {

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

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L1825-1873)
```java
  @Test
  public void exceedMaxMatchNumLimit() throws Exception {

    InitAsset();

    int start = 10;
    int limit = MarketSellAssetActuator.getMAX_MATCH_NUM();
    int step = 1;
    int end = start + step * limit;

    //(sell id_1  and buy id_2)
    String sellTokenId = TOKEN_ID_ONE;
    String buyTokenId = TOKEN_ID_TWO;
    long buyTokenQuant = 400L;
    long sellTokenQuant = buyTokenQuant * (end / start + 1);

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_FIRST);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    accountCapsule.addAssetAmountV2(sellTokenId.getBytes(), sellTokenQuant,
        dbManager.getDynamicPropertiesStore(), dbManager.getAssetIssueStore());
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);
    Assert.assertEquals(sellTokenQuant,
            (long) accountCapsule.getAssetV2MapForTest().get(sellTokenId));

    // Initialize the order book

    // at least limit+1 times
    for (int i = start; i <= end; i += step) {
      addOrder(buyTokenId, (long) start, sellTokenId, i, OWNER_ADDRESS_SECOND);
    }

    // this order(taker) need to match 21 times
    MarketSellAssetActuator actuator = new MarketSellAssetActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_FIRST, sellTokenId, sellTokenQuant, buyTokenId, buyTokenQuant));

    String errorMessage =
        "Too many matches. MAX_MATCH_NUM = " + MarketSellAssetActuator.getMAX_MATCH_NUM();
    try {
      TransactionResultCapsule ret = new TransactionResultCapsule();
      actuator.validate();
      actuator.execute(ret);
      fail(errorMessage);
    } catch (ContractExeException e) {
      Assert.assertEquals(errorMessage, e.getMessage());
    } catch (Exception e) {
      Assert.assertTrue(false);
    }
  }
```
