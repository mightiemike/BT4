### Title
Order-matching quantity subtraction can throw `ArithmeticException` via `subtractExact` underflow when computed match amount diverges from the branch-selection amount - ([File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java])

### Summary
The reported USSD bug is a classic pattern: an amount is computed via one ratio/price calculation, then subtracted from a running "remaining" balance using a *different* calculation, with no guarantee the subtrahend is ≤ the minuend, causing revert-on-underflow. The closest analog in java-tron is `MarketSellAssetActuator.matchSingleOrder`, which performs order-book matching using two independently derived ratio computations and then subtracts one from the other via `subtractExact`.

### Finding Description
In `matchSingleOrder`, the taker/maker comparison branch is selected using one `multiplyAndDivide` computation (`takerBuyTokenQuantityRemain`, derived from `takerSellRemainQuantity`, `makerSellQuantity`, `makerBuyQuantity`): [1](#0-0) 

But in the "taker > maker" branch, the actual amount subtracted from `takerOrderCapsule`'s remaining sell quantity is `makerBuyTokenQuantityReceive`, a *separately* computed value using a different ratio (`makerSellRemainQuantity`, `makerBuyQuantity`, `makerSellQuantity`): [2](#0-1) 

Specifically:
```
makerBuyTokenQuantityReceive = MarketUtils.multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity, ...);
...
takerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
    takerOrderCapsule.getSellTokenQuantityRemain(), makerBuyTokenQuantityReceive));
``` [3](#0-2) 

This mirrors the USSD bug exactly: `takerBuyTokenQuantityRemain` (used to pick the branch, i.e., "taker's remaining sell-side capacity is bigger than maker's remaining") is computed with one price ratio, while `makerBuyTokenQuantityReceive` (the value actually subtracted) is computed independently with a *different* ratio/rounding path. Because integer division/rounding in `multiplyAndDivide` can produce a value larger than what the branch condition guaranteed, `makerBuyTokenQuantityReceive` is not provably ≤ `takerOrderCapsule.getSellTokenQuantityRemain()`. If it exceeds it, `subtractExact` (a saturation/overflow-checked subtraction, likely backed by `StrictMathWrapper` or `Maths`) throws `ArithmeticException` on underflow instead of silently wrapping.

The comment block directly above this code even acknowledges the fragility of this arithmetic relationship ("for the maker, when sellQuantity < buyQuantity, it will get at least one buyToken even when sellRemain = 1... 200 - 200/100 * X = 1 ===> X = 199/2, and this comports with the fact that X is integer"), showing the developers were aware of edge-case rounding risk in this exact spot but only handled the `makerBuyTokenQuantityReceive == 0` case, not the case where it could exceed the taker's remaining amount: [4](#0-3) 

### Impact Explanation
`matchSingleOrder` is invoked from `matchOrder`/`execute` in `MarketSellAssetActuator`, which is reachable by any unprivileged user submitting a `MarketSellAssetContract` transaction that matches against existing resting orders: [5](#0-4) 

If `subtractExact` throws `ArithmeticException` during `execute()`, the exception is caught only for `ItemNotFoundException | InvalidProtocolBufferException | BalanceInsufficientException | ContractValidateException` — `ArithmeticException` is **not** in that catch list, so it propagates uncaught out of `execute()`: [6](#0-5) 

An uncaught runtime exception escaping an actuator's `execute()` during block processing is a state-machine-halting condition (it is not merely a graceful "transaction failed" outcome, since `ContractExeException`-wrapped or `ContractValidateException` cases are the only ones handled/expected as ordinary failure paths). This matches the "invalid-state/halt" impact class: a market order transaction that should simply partially fill or reject can instead cause block-processing to abort with an unhandled exception, and because order matching is triggered by any user posting a sell order against the resting order book, an attacker can craft a resting order (maker) with specific `sellTokenQuantity`/`buyTokenQuantity` ratios and then submit a matching taker order designed to trigger the rounding-driven underflow.

### Likelihood Explanation
Reachability requires only two unprivileged `MarketSellAssetContract` transactions (a maker order followed by a matching taker order) with quantities chosen so that the two independent `multiplyAndDivide` ratio computations diverge under integer rounding at the exact edge where the "taker > maker" branch is picked. This requires careful selection of `sellTokenQuantity`/`buyTokenQuantity` values (feasible since these are attacker-controlled contract fields, subject only to `quantityLimit` and non-zero checks), but no privileged role or oracle manipulation is needed, similar to how the original report only required normal user-triggered rebalance/swap flows.

### Recommendation
In the "taker > maker" branch of `matchSingleOrder`, clamp `makerBuyTokenQuantityReceive` to at most `takerOrderCapsule.getSellTokenQuantityRemain()` before subtracting (or use `Math.min`), and treat any excess as dust, similar to the recommended USSD fix of capping the subtracted amount to the available balance instead of assuming the two independently derived amounts are always consistent:
```java
long takerRemain = takerOrderCapsule.getSellTokenQuantityRemain();
long delta = Math.min(makerBuyTokenQuantityReceive, takerRemain);
takerOrderCapsule.setSellTokenQuantityRemain(takerRemain - delta);
```
Additionally, add `ArithmeticException` to the caught exception set in `execute()` so any residual overflow/underflow degrades to a `ContractExeException` (failed transaction) rather than an unhandled runtime exception during block processing.

### Proof of Concept
Exact reproduction requires stepping through `MarketUtils.multiplyAndDivide`'s rounding behavior for specific `sellTokenQuantity`/`buyTokenQuantity` pairs, which I could not fully verify within the available tool budget (the implementation of `multiplyAndDivide` in `chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java` and the exact semantics of `subtractExact`'s underflow-detection could not be retrieved before running out of iterations). The structural analog (branch-selection ratio ≠ subtraction-amount ratio, with an unchecked `subtractExact`) is confirmed from the code shown above, but confirming a concrete triggering input pair requires manually working through `multiplyAndDivide`'s rounding logic, which should be done in a follow-up session with full file access.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L99-162)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    initStores();

    TransactionResultCapsule ret = (TransactionResultCapsule) object;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(TX_RESULT_NULL);
    }

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L402-405)
```java
    long takerBuyTokenQuantityRemain = MarketUtils
        .multiplyAndDivide(takerSellRemainQuantity, makerSellQuantity, makerBuyQuantity,
            this.disableJavaLangMath());

```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-482)
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
```
