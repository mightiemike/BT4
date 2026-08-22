## Confirmed: No per-transaction rollback for native actuators — `processBlock` at `framework/src/main/java/org/tron/core/db/Manager.java:1884-1902` iterates transactions directly with no `try (ISession tmpSession = revokingStore.buildSession())` around each individual `processTransaction` call (that per-tx session wrapping only exists in `generateBlock`, line 1740, for the local-packing path). During normal block application (`processBlock`), all transactions in an already-received block share the single outer session built in `pushBlock` (line 1389). This confirms the actuator-level analog I found is real and not neutralized by an implicit per-transaction revert.

### Title
Partial, unrecoverable state commit in `MarketSellAssetActuator` order matching leads to asset duplication when `MAX_MATCH_NUM` is exceeded mid-loop - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.matchOrder()`/`matchSingleOrder()` assumes that a taker order will never need more than `MAX_MATCH_NUM` maker fills, and defers the limit check until *after* each match has already been persisted to the store. When the limit is exceeded, a `ContractValidateException` is thrown mid-loop — exactly the "assumption that a step will not fail, but it actually can" pattern from the XC20Wrapper report — except here the already-applied side effects (maker balance credits, order removals) are direct, non-reversible `store.put()` writes rather than reverted VM state, so they survive the transaction's failure.

### Finding Description
In `execute()` [1](#0-0) , the taker's sell-side balance is only deducted from an **in-memory** `AccountCapsule` (`transferBalanceOrToken`) and only persisted via `accountStore.put(accountCapsule...)` at the very end of `execute()`. In contrast, `matchOrder()` calls `matchSingleOrder()` in a loop [2](#0-1) , and `matchSingleOrder()` immediately writes maker-side state changes to the store on every iteration — `orderStore.put(makerOrderCapsule...)` and `addTrxOrToken(...)`, the latter of which calls `accountStore.put(...)` directly, crediting the maker's account with tokens [3](#0-2) .

The `MAX_MATCH_NUM` guard is checked only *after* `matchSingleOrder()` has already run and persisted its writes: `matchOrderCount++; if (matchOrderCount > MAX_MATCH_NUM) { throw new ContractValidateException(...); }` [4](#0-3) . This exception propagates out of `matchOrder()`, is caught in `execute()`'s catch block, and converted to `ContractExeException`, marking the whole transaction `FAILED` [5](#0-4) . But by that point several maker accounts have already been credited with tokens (`accountStore.put`) and their orders removed from the order book (`orderStore.put`, `pairPriceToOrderStore` deletions), while `accountStore.put(accountCapsule...)` for the taker's debit at line 148 never executes because control never reaches that line.

Unlike TVM execution (`Program.callToAddress`/`callToPrecompiledAddress`), which stages all balance/token changes in a child `Repository`/`deposit` that must be explicitly `.commit()`ed before persisting [6](#0-5) , `MarketSellAssetActuator` writes directly to the shared stores with no child-repository staging. There is also no per-transaction revoking session guarding this during ordinary block application: `processBlock()` calls `processTransaction()` directly in a loop with no `ISession` per iteration [7](#0-6)  (the per-tx `ISession` wrapping exists only in the local block-generation path, `generateBlock()`, line 1740 — not in `processBlock`/`applyBlock`, which is what runs for blocks received from peers and validated against the ledger).

### Impact Explanation
An attacker can craft a `MarketSellAssetContract` transaction whose sell/buy quantities match against more than `MAX_MATCH_NUM` (20) resting maker orders of small enough size in the order book (achievable by placing many small maker orders beforehand, as demonstrated by the existing `exceedMaxMatchNumLimit` test). When the transaction executes, the first 20 maker orders are matched, their owners' accounts are credited with tokens/TRX and persisted to the store, and their consumed orders are removed — all before the loop discovers it exceeded `MAX_MATCH_NUM` and throws. The transaction is marked `FAILED` and the taker's sell-side balance is never debited (since that `accountStore.put` never runs), yet the maker-side credits already landed in the store. This creates tokens/TRX out of thin air (maker balances increase with no matching taker debit), corrupting account/asset accounting — a stronger and more damaging manifestation of the same root cause described in the XC20Wrapper report (irreversible external effect committed before an assumed-safe step that can actually fail).

### Likelihood Explanation
Reaching this requires only a normal `MarketSellAssetContract` broadcast transaction plus pre-placed resting orders (also ordinary transactions) — no special privileges, no malicious peer/node behavior, and no dependency-only conditions. `MarketSellAssetActuator` is reachable directly from any account via the standard TRX transaction API, and the trigger condition (order book depth exceeding `MAX_MATCH_NUM`) is entirely attacker-controllable by first placing enough small maker orders. The existing `exceedMaxMatchNumLimit` unit test proves the exception path is reachable in practice; what is not covered by that test is verification of the store state for the already-matched maker orders after the exception, which is exactly the gap this bug relies on.

### Recommendation
Defer all store-mutating operations (`orderStore.put`, `accountStore.put`, `pairPriceToOrderStore`/`pairToPriceStore` mutations) inside `matchSingleOrder()`/`matchOrder()` until the entire match is known to be final, or perform the `MAX_MATCH_NUM` bound check *before* calling `matchSingleOrder()` for each candidate maker order rather than after. Alternatively, stage all match results in memory (mirroring the TVM `Repository`/`deposit` child-commit pattern) and only flush them to the underlying stores once `matchOrder()` returns successfully, so a mid-loop `ContractValidateException` cannot leave partially-applied, non-reversible state.

### Proof of Concept
1. Create maker account `M` and place `MAX_MATCH_NUM + 1` (21) small sell orders such as `addOrder(buyTokenId, 10, sellTokenId, i, M)` for `i = 10..30`, as done in the existing `exceedMaxMatchNumLimit` test [8](#0-7) .
2. As taker account `T`, submit a `MarketSellAssetContract` with `sellTokenQuantity`/`buyTokenQuantity` chosen so the match loop needs to consume more than 20 of `M`'s orders to fully fill.
3. Call `actuator.validate(); actuator.execute(ret);` — observe the thrown `ContractExeException("Too many matches...")` per the existing test, but before instrumenting/asserting, additionally inspect `dbManager.getAccountStore().get(M)` and `dbManager.getOrderStore()` state: the first ~20 maker orders will show `SellTokenQuantityRemain == 0`/`State.INACTIVE` and `M`'s buy-token balance will already reflect the credited tokens from `addTrxOrToken`, while `T`'s sell-token balance in the store remains unchanged (never debited), because `accountStore.put` for `T` at `MarketSellAssetActuator.java:148` never executes on this failure path.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L133-148)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L329-359)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L485-491)
```java
    // save makerOrderCapsule
    orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule);

    // add token into account
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);

```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1657-1671)
```java
  }

  public void callToPrecompiledAddress(MessageCall msg,
      PrecompiledContracts.PrecompiledContract contract) {
    returnDataBuffer = null; // reset return buffer right before the call

    if (getCallDeep() == MAX_DEPTH) {
      stackPushZero();
      this.refundEnergy(msg.getEnergy().longValue(), " call deep limit reach");
      return;
    }

    Repository deposit = getContractState().newRepositoryChild();

    byte[] senderAddress = getContextAddress();
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1884-1902)
```java
      for (TransactionCapsule transactionCapsule : block.getTransactions()) {
        rejectExchangeTransaction(transactionCapsule.getInstance());
        if (chainBaseManager.getDynamicPropertiesStore().allowConsensusLogicOptimization()
            && transactionCapsule.retCountIsGreatThanContractCount()) {
          throw new BadBlockException(String.format("The result count %d of this transaction %s is "
                  + "greater than its contract count %d", transactionCapsule.getRetCount(),
              transactionCapsule.getTransactionId(), transactionCapsule.getContractCount()));
        }
        transactionCapsule.setBlockNum(num);
        if (block.generatedByMyself) {
          transactionCapsule.setVerified(true);
        }
        accountStateCallBack.preExeTrans();
        TransactionInfo result = processTransaction(transactionCapsule, block);
        accountStateCallBack.exeTransFinish();
        if (Objects.nonNull(result)) {
          results.add(result);
        }
      }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L1825-1855)
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

```
