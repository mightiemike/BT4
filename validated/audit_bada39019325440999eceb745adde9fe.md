## Analog Found

### Title
Committee-controlled pause of market transactions blocks order cancellation, causing forced adverse settlement with no grace period on resume - (File: `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java`)

### Summary
The TRON on-chain order-book market (`MarketSellAssetContract` / `MarketCancelOrderContract`) can be globally disabled by committee proposal via the `ALLOW_MARKET_TRANSACTION` dynamic parameter. While disabled, `MarketCancelOrderActuator` rejects every cancellation request outright, so users holding resting orders cannot exit or adjust them in response to price movement. When the feature is re-enabled, `MarketSellAssetActuator`'s matching engine resumes immediately and will match any still-resting orders against the current market at their original stale prices, with no grace period for owners to cancel first. This mirrors the reported bug class: an unprivileged, risk-bearing user's only self-protective action is paused, and unwinding of state resumes with immediate, unmitigated adverse impact the instant the pause is lifted.

### Finding Description
`MarketCancelOrderActuator.validate()` hard-fails any cancel attempt while the feature switch is off: [1](#0-0) 

This flag, `supportAllowMarketTransaction`, is a committee-settable dynamic property (`ALLOW_MARKET_TRANSACTION`) that can be toggled on/off via `ProposalCreateContract`, as referenced throughout `ProposalUtil.java` and `DynamicPropertiesStore.java`. [2](#0-1) 

The same guard exists in `MarketSellAssetActuator`, which is responsible for the order-matching/settlement logic (`matchOrder`, `matchSingleOrder`) that executes trades between resting ("maker") and incoming ("taker") orders at the maker's stored price: [3](#0-2) 

While `ALLOW_MARKET_TRANSACTION` is disabled:
- Existing resting orders remain in `MarketOrderStore` / `MarketPairPriceToOrderStore` unchanged.
- Owners cannot call `MarketCancelOrderActuator` to withdraw or reprice their orders in response to market moves, because `validate()` throws `"Not support Market Transaction, need to be opened by the committee"`.
- There is no alternate emergency-exit path for order owners during the pause.

Once the committee re-enables the flag, `MarketSellAssetActuator` immediately resumes matching new taker orders against these untouched maker orders at their original, now possibly stale, prices — with no cooldown/grace window that would let owners cancel first. This is structurally identical to the reported issue: pausing the user's only protective action (`updateMargin` there, `MarketCancelOrder` here) while risk accrues, then instantly resuming state-changing settlement (`liquidation` there, `order matching/execution` here) without giving the affected party a chance to react first.

### Impact Explanation
Users with resting orders during a market-transaction pause are exposed to forced, unfavorable execution the moment trading resumes, since they had no way to cancel or reprice stale orders during the pause window. This is an accounting/settlement impact: token exchanges happen at prices the owner would have avoided had they been able to cancel, directly costing them value — analogous to being force-liquidated with no chance to top up margin.

### Likelihood Explanation
Triggering the pause requires a committee proposal to set `ALLOW_MARKET_TRANSACTION` to 0 and later back to 1 — an action outside attacker/normal-user control, but such pauses are a documented, exercised governance capability (used for incident response/maintenance) and thus a realistic operational scenario, not a purely theoretical one. Every unprivileged holder of a resting order at the time of pause/resume is affected without any action of their own.

### Recommendation
Introduce a grace period after `ALLOW_MARKET_TRANSACTION` is re-enabled during which order matching in `MarketSellAssetActuator` is deferred (or resting orders are allowed to be cancelled first) before new matches against pre-existing orders can execute. Alternatively, decouple order cancellation from the same feature flag that gates new trade creation, so `MarketCancelOrderActuator` remains available even when new market activity is paused.

### Proof of Concept
1. Committee submits and passes a proposal setting `ALLOW_MARKET_TRANSACTION = 0`.
2. User A has a resting sell order in the order book (created earlier via `MarketSellAssetContract`).
3. Market conditions move against User A's resting order price. User A attempts `MarketCancelOrderContract` to cancel — `MarketCancelOrderActuator.validate()` rejects it with `"Not support Market Transaction, need to be opened by the committee"`. [4](#0-3) 
4. Committee passes a proposal restoring `ALLOW_MARKET_TRANSACTION = 1`.
5. Immediately after, any user submits a `MarketSellAssetContract` (taker order) that matches User A's stale resting order; `matchSingleOrder` executes the trade at User A's original price with no delay or grace window. [5](#0-4) 
6. User A's order is settled at an unfavorable, stale price they were unable to withdraw or adjust during the pause.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java (L166-171)
```java
    }

    if (!dynamicStore.supportAllowMarketTransaction()) {
      throw new ContractValidateException("Not support Market Transaction, need to be opened by"
          + " the committee");
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1-1)
```java
package org.tron.core.store;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L382-418)
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
```
