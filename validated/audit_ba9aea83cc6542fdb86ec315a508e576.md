### Title
Outdated `MarketCancelFee` charged on order cancellation instead of the fee in effect at order-creation time - (File: actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java)

### Summary
`MarketCancelOrderActuator` charges whatever value `MARKET_CANCEL_FEE` currently holds in `DynamicPropertiesStore` at the moment a user cancels a TRC10 market order, rather than the fee that was in effect when the order was created. Since `MARKET_CANCEL_FEE` is a committee-governable chain parameter that can be changed at any time via a proposal, a user's cancellation can be charged a fee they never agreed to and could not have anticipated when they placed the order — directly analogous to the SecondSwap `unlistVesting` bug, where the penalty fee read at unlist-time could differ from the fee assumed at listing-time.

### Finding Description
When a user creates a sell order via `MarketSellAssetActuator`, no fee is charged or cached at that time (only `MARKET_SELL_FEE` bps calc for the trade). If the user later decides to cancel that order via `MarketCancelOrderContract`, `MarketCancelOrderActuator.calcFee()` simply returns `dynamicStore.getMarketCancelFee()`: [1](#0-0) 

This value is read fresh both in `validate()` (balance sufficiency check) and `execute()` (actual deduction): [2](#0-1) [3](#0-2) 

Critically, `MARKET_CANCEL_FEE` is not a fixed protocol constant — it is a committee-adjustable parameter validated and applied through `ProposalUtil` and `ProposalService`: [4](#0-3) 

Because super representatives can pass a proposal to raise `MARKET_CANCEL_FEE` at any time (subject only to the generic `[0, LONG_VALUE]` range check), any order sitting open in the order book can be cancelled later at a fee that is arbitrarily higher than what existed when the order (and the fee the user implicitly accepted) was placed. There is no snapshot of the fee in `MarketOrderCapsule` at creation time, and no cap or reversion mechanism to protect the user, mirroring the exact root cause identified in the SecondSwap report: “Outdated penalty fee gets charged if the penalty fee has changed since listing.”

### Impact Explanation
A malicious or even benign governance action (a passed committee proposal increasing `MARKET_CANCEL_FEE`) can retroactively increase the cost of cancelling pre-existing orders. Users who placed orders under a low-fee regime have no way to avoid the new higher fee except by not cancelling — effectively locking their TRC10 tokens on the market or forcing them to overpay TRX beyond what they agreed to when listing. This is an accounting/fairness defect that directly affects users' funds in a live production financial primitive (java-tron's on-chain TRC10 exchange market), consistent with the "unfair value difference caused by the same penalty fee mechanism" class of bug (Medium severity per the original report's judge).

### Likelihood Explanation
Reaching this path requires only a standard, permissionless, broadcast `MarketSellAssetContract` (to create an order) followed later by a `MarketCancelOrderContract` (to cancel it) — both are ordinary user transactions with no special privilege required. The only prerequisite for the fee to actually change is a successful committee proposal to update `MARKET_CANCEL_FEE`, which is part of normal, expected on-chain governance activity (not an attacker exploiting a vulnerability in the proposal system itself). Given that proposals routinely adjust various dynamic fee parameters over the life of the chain, and market orders can remain open indefinitely, the window for a fee change to occur between order creation and cancellation is realistic.

### Recommendation
Snapshot the applicable cancellation fee into the `MarketOrderCapsule` at order-creation time (in `MarketSellAssetActuator`) and have `MarketCancelOrderActuator.calcFee()` read this cached, order-specific value instead of the live `dynamicStore.getMarketCancelFee()`. Alternatively, apply a fee cap/floor tied to the fee at listing time, or emit an explicit warning/require a matching fee parameter at cancellation to protect users from unexpected fee changes, consistent with the mitigation suggested in the original report (cache the fee at listing time).

### Proof of Concept
1. User broadcasts `MarketSellAssetContract` to list a TRC10 sell order while `MARKET_CANCEL_FEE = X` (as set via `dynamicStore.saveMarketCancelFee(X)`), see test setup pattern: [5](#0-4) 
2. Before the user cancels, a super representative proposal passes raising `MARKET_CANCEL_FEE` to `Y > X` via `ProposalService`/`ProposalUtil` validated parameter update.
3. User broadcasts `MarketCancelOrderContract` for their existing order. `MarketCancelOrderActuator.calcFee()` now returns `Y` instead of `X`, and the user's account is debited `Y` TRX (or the order cannot be cancelled if `Y` exceeds balance, per the `validate()` check at line 213-216), even though they only anticipated paying `X` when they listed the order.
4. This can be confirmed by inspecting the existing test `noEnoughBalance` and the `checkFee` assertions across `MarketCancelOrderActuatorTest`, which show the fee is read directly from `dynamicStore.getMarketCancelFee()` at cancel time with no reference to a value captured at listing time: [6](#0-5)

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L212-216)
```java
    // Whether the balance is enough
    long fee = calcFee();
    if (ownerAccount.getBalance() < fee) {
      throw new ContractValidateException("No enough balance !");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L226-229)
```java
  @Override
  public long calcFee() {
    return dynamicStore.getMarketCancelFee();
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L17-22)
```java
public class ProposalUtil {

  protected static final long LONG_VALUE = 100_000_000_000_000_000L;
  protected static final String BAD_PARAM_ID = "Bad chain parameter id";
  private static final String LONG_VALUE_ERROR =
      "Bad chain parameter value, valid range is [0," + LONG_VALUE + "]";
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketCancelOrderActuatorTest.java (L323-339)
```java
    // set fee
    dbManager.getDynamicPropertiesStore().saveMarketCancelFee(1L);

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.fail("No enough balance !");
    } catch (ContractValidateException e) {
      Assert.assertEquals("No enough balance !", e.getMessage());
    } catch (ContractExeException e) {
      Assert.fail();
    } finally {
      // reset fee
      dbManager.getDynamicPropertiesStore().saveMarketCancelFee(0L);
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketCancelOrderActuatorTest.java (L454-459)
```java
    //check fee
    accountCapsule = accountStore
        .get(ByteArray.fromHexString(OWNER_ADDRESS_FIRST));

    Assert.assertEquals(balanceBefore,
        dbManager.getDynamicPropertiesStore().getMarketCancelFee() + accountCapsule.getBalance());
```
