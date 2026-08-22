Based on the analysis, the strongest reachable analog in java-tron to the reported "oracle can be sandwiched" issue is TRON's on-chain Bancor-style TRC10 Exchange, where trade execution price is computed synchronously and visibly from mempool-broadcast contracts.

### Title
Bancor-formula Exchange trades are visible in the mempool and can be front-run/sandwiched to extract value from other traders - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The external report describes a MEV sandwich against an oracle whose price update transaction is visible in the mempool before it lands on-chain, letting a searcher trade against the stale price for guaranteed profit. java-tron's TRC10 `Exchange` feature (`ExchangeCreate`/`ExchangeInject`/`ExchangeWithdraw`/`ExchangeTransaction`) has the analogous property: the exchange rate is a deterministic Bancor-relay-style function of the on-chain pool balances stored in `ExchangeCapsule`, computed at execution time inside `ExchangeCapsule.transaction()` [1](#0-0) . Because any broadcast `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract` is publicly visible before inclusion, an unprivileged party observing the mempool can react to a pending large trade (or inject/withdraw) by front-running or back-running it in the same block window, extracting value from the price impact exactly like the sandwich pattern described in the report.

### Finding Description
`ExchangeTransactionActuator.execute` calls `exchangeCapsule.transaction(tokenID, tokenQuant, ...)`, which mutates the pool balances and returns the counter-token amount using the constant-relay Bancor curve implemented in `ExchangeProcessor`/`SafeExchangeProcessor` [2](#0-1) . The only protection against adverse price movement is a single-sided minimum-output check (`tokenExpected`) performed in `doValidate()`: [3](#0-2) 
This is a static slippage floor supplied by the trader at submission time based on the pool state they observed when constructing the transaction — it does not account for pool-state changes caused by other transactions that land earlier in the same block. A searcher watching the mempool for a pending `ExchangeTransactionContract`/`ExchangeInjectContract` can submit competing transactions (e.g., a large trade in the same direction just before the victim's, followed by an unwind just after) to move the Bancor curve and capture the price differential, as long as the victim's `tokenExpected` floor is still satisfied. This mirrors the report's dynamic exactly: an on-chain, mempool-visible state transition determines the effective "price," and reordering around it is profitable when there is no TWAP, no per-block price-impact cap, and no commit/reveal or private-order mechanism.

### Impact Explanation
A successful sandwich transfers value from ordinary TRC10 exchange users to the searcher/witness, degrading the effective execution price users receive without any state corruption or consensus divergence — this is an economic/accounting-fairness issue rather than a safety-critical one, since `ExchangeCapsule.transaction()` still enforces non-negative balances and the `balanceLimit` check in `ExchangeInjectActuator.doValidate` [4](#0-3) . It does represent a systemic value-extraction vector against all TRC10 Exchange users.

### Likelihood Explanation
Likelihood is bounded by how much the TRC10 `Exchange` feature is actually used (it has largely been superseded by the `MarketSellAssetActuator` order-book model in newer java-tron versions [5](#0-4) ), and by whether the acting witness/searcher can reliably control intra-block ordering, which in TRON's single-leader-per-slot model is harder than in fee-market chains like Ethereum. This makes exploitation plausible but not as reliable as the classical Ethereum sandwich pattern the report describes.

### Recommendation
For any pool-based exchange math (TRC10 `Exchange`), consider adding: a per-block/per-transaction price-impact cap, a TWAP-based reference price for slippage checks instead of only trader-supplied `tokenExpected`, or restricting large trades to be split/rate-limited. These are the same mitigations the original report recommends for the oracle (more frequent, harder-to-front-run updates) adapted to an AMM-style price mechanism.

### Proof of Concept
1. Attacker monitors the mempool for a pending `ExchangeTransactionContract` that will meaningfully move `firstTokenBalance`/`secondTokenBalance` in `ExchangeCapsule`.
2. Attacker submits a transaction in the same direction just ahead of the victim's, shifting the Bancor curve (`ExchangeProcessor.exchange`) unfavorably for the victim, but not enough to break the victim's `tokenExpected` floor [3](#0-2) .
3. Once the victim's trade executes at the worse rate, attacker submits a reversing trade to capture the price differential, profiting from the temporary imbalance created and resolved within the same or adjacent blocks.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-168)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

    long buyTokenQuant = 0;
    long firstTokenBalance = this.exchange.getFirstTokenBalance();
    long secondTokenBalance = this.exchange.getSecondTokenBalance();
    long newFirstTokenBalance;
    long newSecondTokenBalance;

    if (this.exchange.getFirstTokenId().equals(ByteString.copyFrom(sellTokenID))) {
      buyTokenQuant = processor.exchange(firstTokenBalance,
          secondTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(firstTokenBalance, sellTokenQuant)
          : firstTokenBalance + sellTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(secondTokenBalance, buyTokenQuant)
          : secondTokenBalance - buyTokenQuant;

    } else {
      buyTokenQuant = processor.exchange(secondTokenBalance,
          firstTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(firstTokenBalance, buyTokenQuant)
          : firstTokenBalance - buyTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(secondTokenBalance, sellTokenQuant)
          : secondTokenBalance + sellTokenQuant;

    }

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();

    return buyTokenQuant;
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L229-236)
```java
    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L382-404)
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
```
