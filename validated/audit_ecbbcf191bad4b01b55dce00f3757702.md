### Title
Order-book griefing via `MAX_MATCH_NUM` cap lets an attacker force legitimate `MarketSellAsset` transactions to revert - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
The external report describes a griefing pattern: a fixed, shared numeric cap enforced in a callable-by-anyone contract function, where an attacker can cheaply push shared state toward that cap so that another user's otherwise-valid, fixed-parameter transaction is forced to revert. java-tron's TRC10 market module (`MarketSellAssetActuator`) contains a structurally similar pattern: order matching is capped by a fixed constant `MAX_MATCH_NUM`, and exceeding it during `execute()` throws and fails the transaction, even though the taker's order parameters were fully valid at `validate()` time.

### Finding Description
`MarketSellAssetActuator.matchOrder()` walks the maker order book for the opposite trading pair and matches the taker's order against maker orders one at a time. Every time a maker order is consumed, `matchOrderCount` is incremented, and if it exceeds the fixed constant `MAX_MATCH_NUM`, the actuator throws `ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM)`: [1](#0-0) 

This check is not performed in `validate()` — it is only in the matching loop inside `execute()`, after fee/balance/token has already been deducted from the taker and the order has already been created and persisted: [2](#0-1) 

The number of matches a taker order triggers is entirely a function of *how many maker orders currently exist at/near the matching price* — state that is fully controllable by third parties, not by the taker who submits the order. `MarketSellAssetActuator.validate()` only checks the taker's own quantity against `MarketQuantityLimit` and the taker's own active-order count against `MAX_ACTIVE_ORDER_NUM`; it performs no check related to how many maker orders would need to be matched: [3](#0-2) 

An attacker who wants to block or grief a specific taker order that they observe in the mempool (or generally wants to make matching against a given price level unreliable) can pre-populate the order book with many small maker orders at the price level the taker's order will match against. Because Bandwidth/Energy-based order creation is cheap relative to a large trade, the attacker can seed more than `MAX_MATCH_NUM` maker orders at a favorable price, causing any taker order that would need to walk through all of them to trip the "Too many matches" exception. The existing unit test `exceedMaxMatchNumLimit` demonstrates this exact revert path (a taker order needing to match through more than `MAX_MATCH_NUM` maker orders fails with `ContractExeException`): [4](#0-3) 

### Impact Explanation
This is a griefing/DoS analog to the external report's root cause (public, cheap action that manipulates shared state to push a legitimate, fixed-parameter operation over a hard-coded cap, causing revert). Concretely:
- The victim's transaction is included on-chain and consumed as `FAILED` (fee is still charged per `ret.setStatus(fee, code.FAILED)` in the `catch` block), so the victim loses the market sell fee (`calcFee()` = `MarketSellFee`) without their trade executing.
- The attacker can repeatedly re-seed the price level cheaply relative to the cost imposed on victims, making a given price level effectively unusable for large taker orders, which is a denial-of-service/market manipulation vector on the TRC10 exchange feature.

This differs from the original 423n4 report in one important respect: the attacker here must actually place real maker orders backed by real token balances (there is a per-account `MAX_ACTIVE_ORDER_NUM` cap and orders require asset balance), so the attack has non-trivial cost and is bounded by `MAX_ACTIVE_ORDER_NUM` per account (though can be spread across multiple accounts). This is weaker than the original "1 gwei griefs a 12 ETH stake" scenario, but it is a concrete, reachable divergence between validation-time assumptions and execution-time state that causes fee-consuming reverts for unprivileged users.

### Likelihood Explanation
Medium-low. Exploitation requires the attacker to identify a price level a victim's taker order will hit and to place more than `MAX_MATCH_NUM` small maker orders there ahead of the victim's transaction (front-running via mempool observation or simply pre-seeding a popular trading pair). It requires holding/locking real TRC10 token balances across potentially multiple accounts (to bypass the per-account `MAX_ACTIVE_ORDER_NUM` limit), which raises the cost compared to the original report's 1-gwei griefing, but is still feasible for a motivated attacker on a low-liquidity/thinly-traded TRC10 pair.

### Recommendation
- Move the match-count check (or an equivalent estimate) into `validate()` before any balance is deducted or state is mutated, so a transaction that cannot be fully matched fails cleanly at validation time and does not consume the taker's fee, or at minimum does not create/persist state prior to knowing it will revert.
- Consider allowing partial fill up to `MAX_MATCH_NUM` and returning the remainder to the order book/refunding, instead of reverting the whole transaction when the cap is exceeded.
- Consider rate-limiting or requiring minimum order size for maker orders to raise the cost of seeding many small orders at a single price level, reducing the effectiveness of order-book flooding.

### Proof of Concept
The existing test `exceedMaxMatchNumLimit` in the repo already reproduces the mechanics: [4](#0-3) 
1. Attacker (or attacker-controlled accounts) issue `MAX_MATCH_NUM + 1` small maker `MarketSellAsset` orders at a price that a target victim order is expected to match (sell `TOKEN_ID_TWO` at increasing prices vs `TOKEN_ID_ONE`), as in `addOrder(...)` calls in the test loop.
2. Victim submits a legitimate `MarketSellAsset` transaction (`sellTokenId = TOKEN_ID_ONE`) whose quantity requires matching against more maker orders than `MAX_MATCH_NUM`.
3. `matchOrder()` in `MarketSellAssetActuator.execute()` throws `ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM)` inside `execute()`, which is caught and converted to a `ContractExeException`, causing the transaction to fail with fee already deducted, per [5](#0-4) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L108-162)
```java
    long fee = calcFee();

    try {
      final MarketSellAssetContract contract = this.any
          .unpack(MarketSellAssetContract.class);

      AccountCapsule accountCapsule = accountStore
          .get(contract.getOwnerAddress().toByteArray());

      sellTokenID = contract.getSellTokenId().toByteArray();
      buyTokenID = contract.getBuyTokenId().toByteArray();
      sellTokenQuantity = contract.getSellTokenQuantity();
      buyTokenQuantity = contract.getBuyTokenQuantity();
      MarketPrice takerPrice = MarketPrice.newBuilder()
          .setSellTokenQuantity(sellTokenQuantity)
          .setBuyTokenQuantity(buyTokenQuantity).build();

      // fee
      accountCapsule.setBalance(accountCapsule.getBalance() - fee);
      // add to blackhole address
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      // 1. transfer of balance
      transferBalanceOrToken(accountCapsule);

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

      ret.setOrderId(orderCapsule.getID());
      ret.setStatus(fee, code.SUCESS);
    } catch (ItemNotFoundException
        | InvalidProtocolBufferException
        | BalanceInsufficientException
        | ContractValidateException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L227-239)
```java
    long quantityLimit = dynamicStore.getMarketQuantityLimit();
    if (sellTokenQuantity > quantityLimit || buyTokenQuantity > quantityLimit) {
      throw new ContractValidateException("token quantity must less than " + quantityLimit);
    }

    // check order num
    MarketAccountOrderCapsule marketAccountOrderCapsule = marketAccountStore
        .getUnchecked(ownerAddress);
    if (marketAccountOrderCapsule != null
        && marketAccountOrderCapsule.getCount() >= MAX_ACTIVE_ORDER_NUM) {
      throw new ContractValidateException(
          "Maximum number of orders exceeded，" + MAX_ACTIVE_ORDER_NUM);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L342-359)
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
