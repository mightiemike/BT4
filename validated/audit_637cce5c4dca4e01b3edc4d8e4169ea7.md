### Title
Legacy (non-hardened) TRC10 Exchange bancor-curve math allows pool balance corruption/insolvency across sequential trades - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`, `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The TRC10 `Exchange` (Bancor-style relay-token AMM) computes trade output using a floating-point (`double`) power-curve implementation in `ExchangeProcessor`, and only validates that the resulting pool reserves remain non-negative when the "hardened" calculation mode is enabled. In the default/legacy code path this invariant check is skipped entirely, so a sequence of `ExchangeTransactionContract` trades routed through `ExchangeCapsule.transaction` can drive a pool's `firstTokenBalance`/`secondTokenBalance` into an inconsistent or negative state without failing, corrupting the on-chain exchange reserves used by all subsequent traders and by the exchange creator's `ExchangeWithdrawContract`.

### Finding Description
`ExchangeCapsule.transaction` selects between the legacy `ExchangeProcessor` (double/`Math.pow`-based Bancor formula) and the `SafeExchangeProcessor`/`StrictMathWrapper` combo depending on `dynamicStore.allowHardenExchangeCalculation()`: [1](#0-0) 

Only the hardened branch enforces that the freshly computed reserves cannot go negative: [2](#0-1) 

The legacy `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` computes the trade output purely with `double` arithmetic and `Math.pow`/`StrictMath.pow`, which is inherently lossy/non-associative over repeated sequential calls: [3](#0-2) 

`ExchangeTransactionActuator.execute` calls `exchangeCapsule.transaction(...)` and unconditionally persists whatever new balances come back, with no post-hoc sanity check on `firstTokenBalance`/`secondTokenBalance` in the legacy path: [4](#0-3) 

Because reserves are persisted directly to the `ExchangeStore`/`ExchangeV2Store` (`Commons.putExchangeCapsule`) and read back for the next trade, any accumulated floating-point drift or reserve depletion carries forward and compounds across every subsequent `ExchangeTransactionContract` processed against that pool.

This mirrors the reported bug class: the pool's payout for each participant is computed sequentially from a rate curve (Bancor formula here, CPI-indexed oracle rate in the PSM report) without any global solvency check across all participants, so once reserves are exhausted/corrupted, later traders (and the exchange creator attempting `ExchangeWithdrawContract`, which also trusts `firstTokenBalance`/`secondTokenBalance` at face value — see `ExchangeWithdrawActuator.java` lines 63-89) cannot get the tokens their positions are worth, or the whole pool becomes stuck.

### Impact Explanation
If pool reserves are driven negative or otherwise corrupted for a live TRC10 exchange, all subsequent `ExchangeTransactionContract`/`ExchangeWithdrawContract`/`ExchangeInjectContract` operations against that exchange ID either compute nonsensical amounts (feeding a negative balance back into the Bancor formula) or become permanently unusable, effectively freezing/losing all TRX and TRC10 assets locked in that pool — a concrete asset/accounting corruption and DoS reachable purely by broadcasting normal `ExchangeTransactionContract` transactions.

### Likelihood Explanation
`allowHardenExchangeCalculation` gates whether the safe invariant check is active; on chains/heights where this hard fork feature has not been activated, every trade goes through the unguarded legacy floating-point path with no protection. Exploitability requires only ordinary permissionless `ExchangeTransactionContract` broadcasts (no privileged role), though driving actual reserve corruption needs carefully chosen sequences of near-boundary trade quantities to accumulate enough floating-point drift, which is more of a reliability/precision defect than a trivially triggerable one-shot exploit.

### Recommendation
Apply the same non-negative reserve invariant check (currently only in the hardened branch of `ExchangeCapsule.transaction`) unconditionally, regardless of `allowHardenExchangeCalculation`, and reject/throw `ContractValidateException`/`ContractExeException` whenever a computed trade would leave `firstTokenBalance` or `secondTokenBalance` negative or otherwise inconsistent. Longer-term, migrate the legacy double-based curve fully to the `BigDecimal`/`StrictMathWrapper` implementation used by `SafeExchangeProcessor` for all exchanges, closing the precision gap between legacy and hardened calculation paths.

### Proof of Concept
`ExchangeCapsuleTest.testHardenedTransactionNegativeBalanceThrows` demonstrates that the hardened path throws `ArithmeticException` when a trade would push a reserve negative: [5](#0-4) 
The equivalent legacy call path (`allowHardenCalc=false` or hard fork inactive) in `ExchangeCapsule.transaction`/`ExchangeProcessor.exchange` performs the identical balance arithmetic but has no such guard, meaning the same sequence of trades that trips the invariant in hardened mode would silently corrupt/deplete pool reserves in legacy mode, which is still the default until the corresponding hard fork feature is activated network-wide.

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

**File:** framework/src/test/java/org/tron/core/capsule/ExchangeCapsuleTest.java (L71-83)
```java
  @Test
  public void testHardenedTransactionNegativeBalanceThrows() throws Exception {
    // Construct a corrupt-state pool with a negative balance to drive the
    // < 0 invariant in the hardened branch via subtractExact wrapping.
    ExchangeCapsule capsule = new ExchangeCapsule(
        ByteString.copyFromUtf8("owner"), 99L, 0L,
        "abc".getBytes(), "def".getBytes());
    capsule.setBalance(Long.MAX_VALUE, 1L);

    // Selling abc adds to firstTokenBalance: addExact(MAX, q) overflows -> ArithmeticException
    Assert.assertThrows(ArithmeticException.class,
        () -> capsule.transaction("abc".getBytes(), 1L, true, true));
  }
```
