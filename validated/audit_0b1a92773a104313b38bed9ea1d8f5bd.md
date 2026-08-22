### Title
Legacy (non-hardened) TRC10 Exchange transaction math permits negative pool balances, causing exchange withdrawal/trade DoS - ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
`ExchangeCapsule.transaction()` computes the Bancor-relay AMM swap result and updates the pool balances, but only validates that the resulting pool balances are non-negative when the governance-controlled `allowHardenExchangeCalculation` flag is enabled. In the default/legacy path (flag disabled), this check is skipped entirely, mirroring the `shouldBuyGLP` pattern in the referenced report: a boolean toggle gates the safety logic that keeps the accounted balance consistent with what can actually be withdrawn, and when disabled, later actors relying on that balance can be denied service.

### Finding Description
`ExchangeCapsule.transaction(sellTokenID, sellTokenQuant, useStrictMath, hardenedCalc)` selects between two processors: [1](#0-0) 

The negative-balance guard is conditioned on `hardenedCalc`: [2](#0-1) 

`hardenedCalc` is derived from `AbstractExchangeActuator.allowHarden()`, which reads the dynamic property `allowHardenExchangeCalculation`: [3](#0-2) 

This value is set only via governance proposal (`ProposalUtil`/`ProposalService`), not by default, meaning ordinary chains run with `hardenedCalc = false` unless the committee explicitly opts in. In the legacy path, `ExchangeProcessor` performs the Bancor relay computation using `double` arithmetic: [4](#0-3) 

`ExchangeTransactionActuator.execute()` calls `exchangeCapsule.transaction(...)` and unconditionally applies the resulting balances without an independent invariant check outside the capsule: [5](#0-4) 

Because floating-point rounding in `exchangeToSupply`/`exchangeFromSupply` can, over repeated trades, cause the computed `buyTokenQuant` to diverge from the true invariant-preserving amount, the pool balance for one side can be driven to (or effectively below) zero without any explicit safety check in non-hardened mode. Once a pool balance is exhausted or inconsistent with the AMM's internal accounting, subsequent legitimate trades and withdrawals fail validation, e.g. in `ExchangeWithdrawActuator.doValidate()`: [6](#0-5) 

and in `ExchangeTransactionActuator.doValidate()`, where "token balance is not enough" or exchange being effectively closed (`firstTokenBalance == 0 || secondTokenBalance == 0`) blocks all further participants: [7](#0-6) 

This is directly analogous to the `GlpStrategy` bug: a boolean flag (`shouldBuyGLP` / `allowHardenExchangeCalculation`) gates the logic that keeps the value backing withdrawable shares consistent with actual liquid balance. With the flag off (the default/legacy behavior), later users relying on the exchange pool for withdrawal or trading can be denied service once the pool's accounted balance is exhausted or corrupted by the unguarded floating-point math.

### Impact Explanation
Impact is Medium: it does not directly steal funds through this path alone, but it can corrupt the exchange pool's internal accounting (via unguarded floating-point drift) and deny later traders/withdrawers access to the pool—an accounting/DoS impact within TRC10 Exchange, a core (non-privileged) transaction type reachable by any account holding TRX/TRC10 tokens.

### Likelihood Explanation
Likelihood is Medium: `allowHardenExchangeCalculation` must remain unset (its default/legacy state) for the vulnerable path to be active, and reaching a state where the naive double-precision Bancor math drives a pool balance to zero/negative requires either many small trades or crafted large trades that exploit rounding — no privileged actor is required, only ordinary `ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract` broadcasts.

### Recommendation
Enforce the non-negative pool balance invariant unconditionally in `ExchangeCapsule.transaction()`, independent of the `hardenedCalc`/`allowHardenExchangeCalculation` flag, so legacy (non-hardened) exchange transactions cannot corrupt pool state. Alternatively, deprecate the non-hardened path entirely and always route exchange math through `SafeExchangeProcessor` with the invariant check applied.

### Proof of Concept
1. Ensure `allowHardenExchangeCalculation` is left at its default value (unset/legacy mode), which existing tests explicitly toggle off/on (`dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0)` seen throughout `ExchangeTransactionActuatorTest`/`ExchangeInjectActuatorTest`), confirming legacy mode is the baseline. [8](#0-7) 
2. Create/inject a small TRC10 exchange pool and repeatedly submit small `ExchangeTransactionContract` trades against it, each computed via `ExchangeProcessor`'s `double`-based Bancor formula (no `hardenedCalc` check).
3. Because `hardenedCalc` is false, the `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` guard in `ExchangeCapsule.transaction()` is skipped, so accumulated rounding drift is never rejected. [9](#0-8) 
4. Once one side of the pool balance is depleted (or below the amount required for a legitimate withdrawal/trade), subsequent `ExchangeWithdrawContract`/`ExchangeTransactionContract` calls from other, unrelated users fail validation with "exchange balance is not enough" or "Token balance in exchange is equal with 0, the exchange has been closed", denying them service. [7](#0-6) 

Note: I was unable to fully verify the exact numerical magnitude of floating-point drift required to trigger a real depletion scenario within the current index (this would require running the `ExchangeProcessor` computation empirically over many iterations), so the practical likelihood of reaching an exploitable drift solely from rounding (versus requiring adversarially crafted large single trades) is not conclusively confirmed from static review alone.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-146)
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

```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L158-166)
```java
    }

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-96)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-223)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    BigDecimal bigFirstTokenBalance = new BigDecimal(String.valueOf(firstTokenBalance));
    BigDecimal bigSecondTokenBalance = new BigDecimal(String.valueOf(secondTokenBalance));
    BigDecimal bigTokenQuant = new BigDecimal(String.valueOf(tokenQuant));
    final boolean allowHarden = allowHarden();
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1877-1906)
```java
  @Test
  public void hardenedExecuteOverflowThrowsArithmeticException() throws Exception {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(1);
    dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(1);
    InitExchangeSameTokenNameActive();

    long exchangeId = 1;
    // Corrupt pool to near-MAX TRX so addExact overflows when buying.
    ExchangeCapsule pool = dbManager.getExchangeV2Store().get(ByteArray.fromLong(exchangeId));
    pool.setBalance(Long.MAX_VALUE - 5L, 10_000_000L);
    dbManager.getExchangeV2Store().put(pool.createDbKey(), pool);

    String tokenId = "_";
    long quant = 100L;
    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, 1));

    try {
      // addExact throws ArithmeticException, which is wrapped into ContractExeException.
      Assert.assertThrows(ContractExeException.class,
          () -> actuator.execute(new TransactionResultCapsule()));
    } finally {
      dbManager.getExchangeStore().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeStore().delete(ByteArray.fromLong(2L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(2L));
      dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
    }
  }
```
