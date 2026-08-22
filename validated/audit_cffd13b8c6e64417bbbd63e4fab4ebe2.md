I have confirmed the analog. `MarketCancelOrderActuator.calcFee()` reads `dynamicStore.getMarketCancelFee()` live at execution time [1](#0-0) , and `MARKET_CANCEL_FEE` can be changed at any time by a committee proposal via `ProposalService.process` (`MARKET_CANCEL_FEE` case) [2](#0-1) . Since a resting sell order can sit in the order book for an arbitrary number of blocks between creation (`MarketSellAssetActuator.execute`) and cancellation, the fee charged at cancel time is whatever `MARKET_CANCEL_FEE` happens to be at that later block, not the fee in effect when the order was opened — this is the exact bug class from the report (fee mutated during the lifetime of an open order). Unlike `ENERGY_FEE`/`TRANSACTION_FEE`, which java-tron snapshots into `EnergyPriceHistory`/`BandwidthPriceHistory` specifically to avoid this class of issue [3](#0-2) [4](#0-3) , `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` have no such history mechanism, so the resulting unpredictability is a genuine, reachable analog. Below is the finding.

### Title
Market order cancellation fee is read live instead of fixed at order-open time, letting governance-driven fee changes make cancellation cost unpredictable - (File: actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java)

### Summary
`MarketCancelOrderActuator` charges `MARKET_CANCEL_FEE` at the time a resting order is cancelled, not the fee that was in effect when the order was opened. Because `MARKET_CANCEL_FEE` is a committee-controlled dynamic parameter that can be changed by proposal at any time while orders remain resting in the order book, a trader's actual cancellation cost is unpredictable and can diverge arbitrarily from what they expected when placing the order — mirroring the reported Aragon Bancor market maker issue where fees applied at claim time could differ from fees at order-open time.

### Finding Description
`MarketSellAssetActuator.execute` creates and stores an order (`createAndSaveOrder`) that may remain resting in `MarketOrderStore`/`MarketPairPriceToOrderStore` for an unbounded number of blocks if it does not fully match [5](#0-4) . When the owner later cancels that order via `MarketCancelOrderActuator.execute`, the fee charged is computed by `calcFee()`, which simply returns the *current* value of `dynamicStore.getMarketCancelFee()` at execution time [6](#0-5) [1](#0-0) . The order itself (`MarketOrderCapsule`) does not persist the fee rate that applied when it was created.

`MARKET_CANCEL_FEE` is a proposal-adjustable chain parameter: any witness-approved proposal invokes `ProposalService.process`, which calls `dynamicStore.saveMarketCancelFee(entry.getValue())` immediately upon approval, with no history/versioning kept for this parameter (unlike `ENERGY_FEE`/`TRANSACTION_FEE`, which are snapshotted into price-history strings for exactly this reason) [2](#0-1) [7](#0-6) . `ProposalUtil.validator` only bounds the value to `[0, 10_000_000_000L]`; it imposes no restriction tying the new fee to previously opened orders [8](#0-7) .

The same live-read pattern also exists for `MARKET_SELL_FEE` in `MarketSellAssetActuator.calcFee()` [9](#0-8) , but that fee is charged atomically at order creation in the same transaction, so there is no "open now, pay later" gap there. The cancellation path is the one with an unbounded time gap between order-open and fee-payment, which is the exact shape of the reported bug class.

### Impact Explanation
A user who opens a sell order under one `MARKET_CANCEL_FEE` value has no guarantee about the fee they will actually pay if they need to cancel that order later. A committee/witness majority can raise `MARKET_CANCEL_FEE` (up to `10_000_000_000` sun, i.e., 10,000 TRX) after users have placed resting orders, directly increasing the real cost of exiting those orders without the user's consent, or a validation failure ("No enough balance!") could unexpectedly block cancellation entirely if the account's balance no longer covers the new higher fee, effectively trapping the order (and the escrowed sell-token balance) until the account is topped up. This is an accounting/economic-fairness issue affecting order owners broadly, not a single privileged actor.

### Likelihood Explanation
Triggering this requires only a normal governance proposal changing `MARKET_CANCEL_FEE` (a supported, already-implemented mechanism) while any user has a resting market order open — a routine, low-friction real-world scenario, not an edge case requiring a malicious/leaked key or privileged bypass. The market order book naturally holds orders open across many blocks (that is its purpose), giving broad time windows for fee changes to land.

### Recommendation
Snapshot the applicable cancel fee (and/or sell fee) into the `MarketOrderCapsule` at order-creation time (mirroring the `EnergyPriceHistory`/`BandwidthPriceHistory` pattern already used for `ENERGY_FEE`/`TRANSACTION_FEE`), and have `MarketCancelOrderActuator.calcFee()` read the fee stored on the order rather than the live `dynamicStore.getMarketCancelFee()` value, so that the fee for an order is fixed for its entire lifetime as recommended by the referenced report.

### Proof of Concept
1. Committee proposes and passes `MARKET_CANCEL_FEE = 0`.
2. User A submits `MarketSellAssetContract` to sell TOKEN_A for TOKEN_B at a given price; the order does not fully match and rests in the order book (`MarketSellAssetActuator.execute` → `saveRemainOrder`) [10](#0-9) .
3. Committee proposes and passes `MARKET_CANCEL_FEE = 10_000_000_000` (max allowed) via `ProposalService.process` [2](#0-1) .
4. User A submits `MarketCancelOrderContract` to cancel the still-resting order. `MarketCancelOrderActuator.execute` charges `calcFee()` = current `dynamicStore.getMarketCancelFee()` = 10,000 TRX, an amount the user never agreed to when opening the order, and if the account balance is insufficient, `validate()` throws "No enough balance !" [11](#0-10) , leaving the order (and locked sell-token balance) stuck.

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

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L44-51)
```java
        case TRANSACTION_FEE: {
          manager.getDynamicPropertiesStore().saveTransactionFee(entry.getValue());
          // update bandwidth price history
          manager.getDynamicPropertiesStore().saveBandwidthPriceHistory(
              manager.getDynamicPropertiesStore().getBandwidthPriceHistory()
                  + "," + proposalCapsule.getExpirationTime() + ":" + entry.getValue());
          break;
        }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L83-90)
```java
        case ENERGY_FEE: {
          manager.getDynamicPropertiesStore().saveEnergyFee(entry.getValue());
          // update energy price history
          manager.getDynamicPropertiesStore().saveEnergyPriceHistory(
              manager.getDynamicPropertiesStore().getEnergyPriceHistory()
                  + "," + proposalCapsule.getExpirationTime() + ":" + entry.getValue());
          break;
        }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L232-235)
```java
        case MARKET_CANCEL_FEE: {
          manager.getDynamicPropertiesStore().saveMarketCancelFee(entry.getValue());
          break;
        }
```

**File:** framework/src/main/java/org/tron/core/db/api/EnergyPriceHistoryLoader.java (L39-55)
```java
  public void getEnergyProposals() {
    proposalCapsuleList = chainBaseManager.getProposalStore()
        .getSpecifiedProposals(State.APPROVED, ProposalType.ENERGY_FEE.getCode());
  }

  public String parseProposalsToStr() {
    StringBuilder builder = new StringBuilder(DynamicPropertiesStore.DEFAULT_ENERGY_PRICE_HISTORY);

    for (ProposalCapsule proposalCapsule : proposalCapsuleList) {
      builder.append(",")
          .append(proposalCapsule.getExpirationTime())
          .append(":")
          .append(proposalCapsule.getParameters().get(ProposalType.ENERGY_FEE.getCode()));
    }

    return builder.toString();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L136-147)
```java
      // 2. create and save order
      MarketOrderCapsule orderCapsule = createAndSaveOrder(accountCapsule, contract);

      // 3. match order
      matchOrder(orderCapsule, takerPrice, ret, accountCapsule);

      // 4. save remain order into order book
      if (orderCapsule.getSellTokenQuantityRemain() != 0) {
        saveRemainOrder(orderCapsule);
      }

      orderStore.put(orderCapsule.getID().toByteArray(), orderCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L288-291)
```java
  @Override
  public long calcFee() {
    return dynamicStore.getMarketSellFee();
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L384-397)
```java
      case MARKET_CANCEL_FEE: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1)) {
          throw new ContractValidateException("Bad chain parameter id [MARKET_CANCEL_FEE]");
        }
        if (!dynamicPropertiesStore.supportAllowMarketTransaction()) {
          throw new ContractValidateException(
              "Market Transaction is not activated, can not set Market Cancel Fee");
        }
        if (value < 0 || value > 10_000_000_000L) {
          throw new ContractValidateException(
              "Bad MARKET_CANCEL_FEE parameter value, valid range is [0,10_000_000_000L]");
        }
        break;
      }
```
