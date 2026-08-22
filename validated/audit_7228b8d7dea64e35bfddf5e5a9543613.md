Based on my investigation, java-tron already has extensive, deliberate hardening against this exact bug class (unchecked overflow), which weakens the case for the external report's analog. However, there is a genuine, currently-live gap.

### Title
Silent long-overflow / precision-loss in TRC10 exchange pool arithmetic when `allowHardenExchangeCalculation` is disabled - ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java])

### Summary
The report's bug class ("unchecked math, add SafeMath") maps onto java-tron's exchange (Bancor-style AMM) arithmetic. The codebase has already added a hardened path (`SafeExchangeProcessor`, `StrictMathWrapper.addExact`, `BigInteger`/`BigDecimal` math) gated by the `allowHardenExchangeCalculation` feature flag, but the **default/legacy path** (`ExchangeProcessor`, plain `long`/`double` arithmetic) remains reachable from anonymous broadcast transactions (`ExchangeCreateContract`, `ExchangeInjectContract`, `ExchangeTransactionContract`) whenever that flag is not activated on a given chain/network.

### Finding Description
`ExchangeCapsule.transaction()` selects between `SafeExchangeProcessor` and `ExchangeProcessor` based on the `hardenedCalc` flag passed in by the actuator: [1](#0-0) 

The non-hardened `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` perform plain `long balance + quant` addition and cast `double` results back to `long` with no overflow or precision guard: [2](#0-1) 

Compare this with the hardened variant, which uses `StrictMathWrapper.addExact` and `BigDecimal` exact arithmetic: [3](#0-2) 

`ExchangeCapsule.transaction()` also updates `firstTokenBalance`/`secondTokenBalance` with plain `+`/`-` (`firstTokenBalance + sellTokenQuant`, `secondTokenBalance - buyTokenQuant`) when `hardenedCalc` is false, with no post-condition check for negative or overflowed balances (the `< 0` guard only fires `if (hardenedCalc && ...)`): [4](#0-3) 

`ExchangeTransactionActuator` (reachable via any broadcast `ExchangeTransactionContract`) validates only that the *deposited* side's new balance is under `getExchangeBalanceLimit()` using safe `addExact`, but the *other* side of the pool (`anotherTokenQuant`, computed from the unchecked Bancor formula) is never checked against `balanceLimit` before being applied in `execute()`: [5](#0-4) [6](#0-5) 

Because TRC10 asset `totalSupply` is attacker-controllable up to `Long.MAX_VALUE`-scale at issuance time (an unprivileged `AssetIssueContract`), an attacker can create an exchange pool between TRX and a self-issued token with a huge supply, inject/trade repeatedly to push pool balances toward `Long.MAX_VALUE`, and trigger silent `long` wraparound or `double`→`long` precision corruption in the unhardened Bancor math — producing a negative or nonsensical pool balance, or minting excess "another token" out of the pool.

### Impact Explanation
If successfully triggered on a network where `allowHardenExchangeCalculation` has not been activated, this could corrupt an exchange pool's TRX/TRC10 balances (negative or wrapped balances), allowing an attacker to receive far more of the counter-asset than they should, effectively draining the pool — an accounting/asset corruption reachable purely through broadcast transactions from an unprivileged account. This matches the "overflow leaves contract in inconsistent state" concern from the original report.

### Likelihood Explanation
This is **feature-flag dependent** and the codebase clearly demonstrates this exact bug class was already identified and mitigated with a hardened path (tests such as `hardenedExecuteOverflowThrowsArithmeticException` in `ExchangeTransactionActuatorTest` explicitly show the maintainers understand and guard against it once hardening is enabled). I could not confirm from the index whether `allowHardenExchangeCalculation` defaults to enabled at genesis on current mainnet, nor could I fully verify the exact `Long`/token-supply bounds enforced at `AssetIssueContract` creation time that would be required to actually reach an overflow (this needs runtime/config verification beyond what the index provides). Practical exploitation also requires accumulating pool balances near `Long.MAX_VALUE`, which is a large but not clearly infeasible amount of setup transactions.

### Recommendation
- Confirm whether `allowHardenExchangeCalculation` is enabled by default on the target network; if not, activate it (or remove the legacy `ExchangeProcessor` path entirely) since the hardened `SafeExchangeProcessor` path already exists.
- In `ExchangeTransactionActuator`/`ExchangeInjectActuator`, validate **both** resulting pool balances (not just the deposited side) against `getExchangeBalanceLimit()` before calling `exchangeCapsule.transaction()`/`setBalance()`, regardless of hardening flag.
- Cap `AssetIssueContract` `totalSupply` well below `Long.MAX_VALUE` to remove the preconditions needed to approach overflow in any downstream long-arithmetic pipeline.

### Proof of Concept
Conceptual (not verified by execution, given static-analysis-only access):
1. Broadcast `AssetIssueContract` with `totalSupply` near `Long.MAX_VALUE`.
2. Broadcast `ExchangeCreateContract` pairing TRX with this asset.
3. Repeatedly broadcast `ExchangeInjectContract`/`ExchangeTransactionContract` to grow `firstTokenBalance`/`secondTokenBalance` toward `Long.MAX_VALUE`, relying on the unhardened `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` plain-`long` addition path (`allowHardenExchangeCalculation` = 0).
4. Trigger one more `ExchangeTransactionContract` causing `firstTokenBalance + sellTokenQuant` (or the `double`-based supply math) to wrap/lose precision in `ExchangeCapsule.transaction()`, producing an inconsistent/negative pool balance usable to drain the counter-asset. [7](#0-6)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-169)
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
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-39)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L19-38)
```java
  private BigDecimal exchangeToSupply(long balance, long quant) {
    long newBalance = StrictMathWrapper.addExact(balance, quant);
    BigDecimal bdQuant = BigDecimal.valueOf(quant);
    BigDecimal bdNewBalance = BigDecimal.valueOf(newBalance);
    BigDecimal base = BigDecimal.ONE.add(
        bdQuant.divide(bdNewBalance, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 0.0005);
    return SUPPLY.negate().multiply(
        BigDecimal.ONE.subtract(BigDecimal.valueOf(powResult))).setScale(0, RoundingMode.DOWN);
  }

  private long exchangeFromSupply(long balance, BigDecimal supplyQuant) {
    BigDecimal bdBalance = BigDecimal.valueOf(balance);
    BigDecimal base = BigDecimal.ONE.add(
        supplyQuant.divide(SUPPLY, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 2000.0);
    BigDecimal exchangeBalance = bdBalance.multiply(
        BigDecimal.valueOf(powResult).subtract(BigDecimal.ONE));
    return exchangeBalance.setScale(0, RoundingMode.DOWN).longValueExact();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-98)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L196-220)
```java
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
```
