Confirmed: `AccountStore.get(byte[] key)` returns `null` when the account does not exist, it does not throw [1](#0-0) .

### Title
Unhandled null account causes NullPointerException DoS in `MarketSellAssetActuator.matchSingleOrder`/`addTrxOrToken`/`returnSellTokenRemain` - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
Analogous to the Deriverse `match_orders` bug — where one code path defaults gracefully on a missing/`None` value (`ref_payment`) while a sibling path (`ref_client_id`) unwraps the same kind of value with `.ok_or(...)?` and reverts — `MarketSellAssetActuator` performs some account lookups with a null-check "graceful" pattern (e.g., `MarketAccountOrderCapsule` lookup in `createAndSaveOrder`) but performs other account lookups for the **maker's** account inside `matchOrder`/`matchSingleOrder` without any null check, directly dereferencing the result of `accountStore.get(...)`.

### Finding Description
`AccountStore.get(byte[] key)` returns `null` for a non-existent key rather than throwing [1](#0-0) . In `MarketSellAssetActuator`, the private helper methods `addTrxOrToken(MarketOrderCapsule, long)` (the maker overload) and `returnSellTokenRemain(MarketOrderCapsule)` fetch the maker's account via `accountStore.get(orderCapsule.getOwnerAddress().toByteArray())` and immediately call `.setBalance(...)` / pass it into `MarketUtils.returnSellTokenRemain(...)` without checking for `null` [2](#0-1) . These are invoked from `matchSingleOrder`, which is itself called from `matchOrder`, both reached during normal execution of the swap/sell-asset instruction (`MarketSellAssetActuator.execute`) [3](#0-2) [4](#0-3) .

In contrast, elsewhere in the same class (`createAndSaveOrder`), the code correctly guards against a missing/null record before use: `if (marketAccountOrderCapsule == null) { marketAccountOrderCapsule = new MarketAccountOrderCapsule(...); }` [5](#0-4) . This is the same inconsistent-handling pattern flagged in the external report: one lookup path defaults gracefully, a sibling lookup path in the same flow does not, and blows up.

The `execute()` method's catch block only handles `ItemNotFoundException | InvalidProtocolBufferException | BalanceInsufficientException | ContractValidateException` [6](#0-5) ; a `NullPointerException` thrown from `addTrxOrToken`/`returnSellTokenRemain` is NOT one of those, so it propagates as an uncaught `RuntimeException` out of `execute()`.

### Impact Explanation
Since maker order accounts are looked up by whatever `owner_address` was stored on the resting `MarketOrderCapsule` at order-creation time, this path is normally populated by the actuator's own `createAndSaveOrder`, so under ordinary conditions the maker account should exist. However, if the maker account can be pruned/removed (e.g., account permission/asset cleanup elsewhere in the codebase, or any future migration/consensus edge case that deletes the account row while orders referencing it remain in the order book) or if a resting order's stored owner data can otherwise diverge from `AccountStore` state, the very first taker order that matches against that order will throw an uncaught NPE during block/transaction processing. Because the exception is not one of the enumerated catch types, it is not converted into a normal `ContractExeException`/failed-transaction result — it escapes as a `RuntimeException`, which can crash transaction processing for that block on every node that re-executes/re-applies it, producing a consensus-halting DoS (similar in class to the reported permanent DoS on the swap instruction).

### Likelihood Explanation
Likelihood depends on whether an account referenced by a resting maker order can become unavailable/null in `AccountStore` while the order itself persists in the order book — this requires further confirmation against account-deletion logic in the broader codebase (out of the scope I could fully verify with the available tools). Given the index-limited exploration performed, I could not confirm a concrete transaction sequence that deletes an account while it still has open market orders; this weakens confidence relative to the original report where `header` being `None` was a directly reachable, common condition.

### Recommendation
Add a defensive null check for the account fetched from `accountStore.get(orderCapsule.getOwnerAddress().toByteArray())` in `addTrxOrToken(MarketOrderCapsule, long)` and `returnSellTokenRemain(MarketOrderCapsule)`, matching the graceful-handling pattern already used in `createAndSaveOrder`. If the account is unexpectedly missing, throw a checked exception type (e.g., `ItemNotFoundException`) that is already caught and translated into a failed transaction (`ContractExeException`) rather than allowing an unchecked `NullPointerException`/`RuntimeException` to propagate out of `execute()`.

### Proof of Concept
Not independently reproducible from the indexed context alone — a full PoC would require confirming (via a live Devin session with repo/test access) a state transition that leaves an `AccountStore` entry absent for an address still referenced by an active `MarketOrderCapsule`, then submitting a matching `MarketSellAssetContract` transaction that reaches `matchSingleOrder` → `addTrxOrToken`/`returnSellTokenRemain` for that maker order.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AccountStore.java (L61-65)
```java
  @Override
  public AccountCapsule get(byte[] key) {
    byte[] value = revokingDB.getUnchecked(key);
    return ArrayUtils.isEmpty(value) ? null : new AccountCapsule(value);
  }
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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L307-360)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L382-490)
```java
  // return all match or not
  private void matchSingleOrder(MarketOrderCapsule takerOrderCapsule,
      MarketOrderCapsule makerOrderCapsule, TransactionResultCapsule ret,
      AccountCapsule takerAccountCapsule)
      throws ItemNotFoundException {

    long takerSellRemainQuantity = takerOrderCapsule.getSellTokenQuantityRemain();
    long makerSellQuantity = makerOrderCapsule.getSellTokenQuantity();
    long makerBuyQuantity = makerOrderCapsule.getBuyTokenQuantity();
    long makerSellRemainQuantity = makerOrderCapsule.getSellTokenQuantityRemain();

    // according to the price of maker, calculate the quantity of taker can buy
    // for makerPrice,sellToken is A,buyToken is TRX.
    // for takerPrice,buyToken is A,sellToken is TRX.

    // makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX =
    //   takerBuyTokenQuantityCurrent_A/takerSellTokenQuantityRemain_TRX
    // => takerBuyTokenQuantityCurrent_A = takerSellTokenQuantityRemain_TRX *
    //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX

    long takerBuyTokenQuantityRemain = MarketUtils
        .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
            this.disableJavaLangMath());

    if (takerBuyTokenQuantityRemain == 0) {
      // quantity too small, return sellToken to user
      takerOrderCapsule.setSellTokenQuantityReturn();
      MarketUtils.returnSellTokenRemain(takerOrderCapsule, takerAccountCapsule,
          dynamicStore, assetIssueStore);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);
      return;
    }

    long takerBuyTokenQuantityReceive; // In this match, the token obtained by taker
    long makerBuyTokenQuantityReceive; // the token obtained by maker

    if (takerBuyTokenQuantityRemain == makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker == maker

      // makerSellTokenQuantityRemain_A/makerBuyTokenQuantityCurrent_TRX =
      //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX
      // => makerBuyTokenQuantityCurrent_TRX = makerSellTokenQuantityRemain_A *
      //   makerBuyTokenQuantity_TRX / makerSellTokenQuantity_A

      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();

      long takerSellTokenLeft =
          takerOrderCapsule.getSellTokenQuantityRemain() - makerBuyTokenQuantityReceive;
      takerOrderCapsule.setSellTokenQuantityRemain(takerSellTokenLeft);
      makerOrderCapsule.setSellTokenQuantityRemain(0);

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

      makerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
          makerOrderCapsule.getSellTokenQuantityRemain(), takerBuyTokenQuantityRemain));
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

    // save makerOrderCapsule
    orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule);

    // add token into account
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L501-507)
```java
  private MarketOrderCapsule createAndSaveOrder(AccountCapsule accountCapsule,
      MarketSellAssetContract contract) {
    MarketAccountOrderCapsule marketAccountOrderCapsule = marketAccountStore
        .getUnchecked(contract.getOwnerAddress().toByteArray());
    if (marketAccountOrderCapsule == null) {
      marketAccountOrderCapsule = new MarketAccountOrderCapsule(contract.getOwnerAddress());
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L550-570)
```java
  private void addTrxOrToken(MarketOrderCapsule orderCapsule, long num) {
    AccountCapsule accountCapsule = accountStore
        .get(orderCapsule.getOwnerAddress().toByteArray());

    byte[] buyTokenId = orderCapsule.getBuyTokenId();
    if (Arrays.equals(buyTokenId, "_".getBytes())) {
      accountCapsule.setBalance(addExact(accountCapsule.getBalance(), num));
    } else {
      accountCapsule
          .addAssetAmountV2(buyTokenId, num, dynamicStore, assetIssueStore);
    }
    accountStore.put(orderCapsule.getOwnerAddress().toByteArray(), accountCapsule);
  }

  private void returnSellTokenRemain(MarketOrderCapsule orderCapsule) {
    AccountCapsule accountCapsule = accountStore
        .get(orderCapsule.getOwnerAddress().toByteArray());

    MarketUtils.returnSellTokenRemain(orderCapsule, accountCapsule, dynamicStore, assetIssueStore);
    accountStore.put(orderCapsule.getOwnerAddress().toByteArray(), accountCapsule);
  }
```
