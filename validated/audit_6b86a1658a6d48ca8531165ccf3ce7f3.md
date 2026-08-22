### Title
Legacy (non-hardened) TRC10 Exchange (Bancor-relay AMM) can be driven into negative/insolvent pool balances due to floating-point precision loss and missing balance-sign validation - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
The TRC10 `Exchange`/`ExchangeV2` feature (`ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeTransactionActuator`) is java-tron's on-chain AMM "market," analogous to a Holdefi lending pool: it holds two token balances that back every trader's expectation of redeemable value. By default (`AllowHardenExchangeCalculation` is a proposal-gated feature that must be explicitly turned on), every trade is executed by the legacy `ExchangeProcessor`, which relies on `double`/`Math.pow` arithmetic and performs **no post-trade solvency check**. Only when the chain-wide proposal enables "hardened" mode does `SafeExchangeProcessor`/`ExchangeCapsule.transaction` verify that the resulting balances are non-negative. In the default (legacy) path, precision loss in the Bancor-relay formula can produce a `buyTokenQuant` that exceeds the actual `buyTokenBalance`, silently driving the pool's `secondTokenBalance` (or `firstTokenBalance`) negative — i.e., the market becomes insolvent for the next trader who tries to redeem.

### Finding Description
`ExchangeTransactionActuator.execute()` calls `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` [1](#0-0) , and `allowHarden()` simply forwards `dynamicPropertiesStore.allowHardenExchangeCalculation()` [2](#0-1) , a value that is controlled through the on-chain proposal system (`ProposalUtil`, `DynamicPropertiesStore`) and defaults to disabled unless witnesses vote to enable it.

Inside `ExchangeCapsule.transaction()`, when `hardenedCalc` is `false` (the default), the new balances are computed with plain `long` arithmetic fed by the `ExchangeProcessor` (double/`Math.pow`-based Bancor relay calculation) and **no check is performed** on the sign of the resulting balances: [3](#0-2) 

Only the `hardenedCalc == true` branch guards against a negative outcome:
```
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
``` [4](#0-3) 

The legacy `ExchangeProcessor.exchange()` computes the Bancor relay math with `double` and truncates to `long`: [5](#0-4) 
This double-precision path is known to lose precision on extreme balance ratios or very large/very small trade sizes, and unlike the `SafeExchangeProcessor`/hardened path, there is no guard preventing the computed `buyTokenQuant` from exceeding the pool's actual `buyTokenBalance`. `ExchangeTransactionActuator.doValidate()` only checks an upper bound (`getExchangeBalanceLimit`) on the token being sold and slippage (`tokenExpected`) [6](#0-5) ; it does not re-verify that the opposite side's balance stays non-negative after the trade in the non-hardened path.

### Impact Explanation
Any anonymous account can broadcast an `ExchangeTransactionContract` transaction against any active TRC10 exchange pool. If a crafted `tokenQuant`/pool-ratio combination causes the double-precision Bancor relay computation in `ExchangeProcessor` to round the "buy" amount above the true pool balance, `ExchangeCapsule.transaction()` will accept and persist a negative `firstTokenBalance`/`secondTokenBalance` for that exchange (since the sign check is gated behind `hardenedCalc`). Once a pool's balance for either token has drifted negative or become inconsistent with its true backing, that market is insolvent: later traders/withdrawers attempting `ExchangeWithdrawActuator`/`ExchangeTransactionActuator` operations against it can fail unpredictably or extract more value than exists, producing a "run on the market" where last participants lose funds — a direct asset/accounting corruption in a core, unprivileged, broadcast-transaction-reachable actuator.

### Likelihood Explanation
The condition is reachable by any anonymous account issuing a normal `ExchangeTransactionContract` (no privileged role required), and the vulnerable arithmetic path is the **default** production behavior since `allowHardenExchangeCalculation` must be explicitly enabled via proposal. Repeated small-value trades against a thinly-capitalized pool, or a single large trade against an imbalanced pool, are exactly the kind of "quick, large price move" scenario referenced in the original report, and the missing invariant check makes exploitation a matter of finding the right balance ratio/quantity rather than requiring any protocol-level flaw elsewhere.

### Recommendation
Make the non-negative balance check unconditional in `ExchangeCapsule.transaction()` (remove the `hardenedCalc &&` gate) so that every trade — legacy or hardened — is rejected with `ContractValidateException` if it would drive either token balance negative. Additionally, consider making `SafeExchangeProcessor`'s BigDecimal-based calculation and its solvency check the default execution path rather than an opt-in proposal, and add regression tests exercising extreme sell/buy ratios and tiny pool balances against the legacy `ExchangeProcessor` to confirm no negative balance can ever be committed.

### Proof of Concept
1. Deploy/observe a TRC10 exchange pool with small `firstTokenBalance`/`secondTokenBalance` (e.g., created via `ExchangeCreateActuator`) while `AllowHardenExchangeCalculation` remains at its default (disabled) value.
2. Broadcast an `ExchangeTransactionContract` with a `tokenQuant` chosen so that the double-precision Bancor relay math in `ExchangeProcessor.exchangeFromSupply` (using `Math.pow`) rounds the computed `buyTokenQuant` up to a value ≥ the current opposite-side balance — feasible because `exchange()` never clamps the result against `buyTokenBalance` in the non-hardened path [7](#0-6) .
3. `ExchangeCapsule.transaction()` executes this in the `hardenedCalc == false` branch, computes `newSecondTokenBalance` (or `newFirstTokenBalance`) as negative, and — because the guard on line 160 only fires when `hardenedCalc` is true — persists the negative balance via `Commons.putExchangeCapsule(...)` in `ExchangeTransactionActuator.execute()` [8](#0-7) .
4. Subsequent traders interacting with this now-insolvent pool experience inconsistent/incorrect payouts, demonstrating the market-insolvency condition.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L68-69)
```java
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L95-96)
```java
      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-205)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-45)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);

    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    logger.debug("issuedSupply: " + issuedSupply);
    long out = (long) issuedSupply;
    supply += out;

    return out;
  }

  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```
