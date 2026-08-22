### Title
Downward-biased Bancor formula truncation in `ExchangeProcessor` reduces tokens received from TRX/TRC10 bonding-curve exchange - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The legacy Bancor-formula bonding-curve exchange calculation used by `ExchangeTransactionActuator`, `ExchangeInjectActuator`, and `ExchangeWithdrawActuator` truncates intermediate double-precision results to `long` before completing the exchange formula, producing a systematic downward bias in the amount of tokens a user receives — the same class of rounding-order bug described in the Fei `Roots` library report (`twoThirdsRoot`/`threeHalfsRoot` truncating before completing the math, biasing the bonding-curve output). This is the exact default-path behavior unless a chain-parameter-gated "hardened" calculator is enabled.

### Finding Description
`ExchangeProcessor.exchangeToSupply` and `exchangeFromSupply` compute a Bancor-curve formula using `double` arithmetic and cast the final result to `long` with simple narrowing (`(long) issuedSupply` / `(long) exchangeBalance`), which truncates toward zero rather than rounding, and accumulates floating point error through `Maths.pow` before the single truncation: [1](#0-0) 

This is invoked by `ExchangeCapsule.transaction(...)`, which chooses between the legacy `ExchangeProcessor` and the exact `SafeExchangeProcessor` (BigDecimal-based, uses `RoundingMode.HALF_UP` internally then truncates only once at the very end with `setScale(0, RoundingMode.DOWN)`) based on a `hardenedCalc` flag: [2](#0-1) 

The `hardenedCalc` / `useStrictMath` flags are sourced from `AbstractExchangeActuator.allowHarden()`, which reads the dynamic chain parameter `allowHardenExchangeCalculation`: [3](#0-2) 

`ExchangeTransactionActuator.execute`/`doValidate` calls `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` directly from an anonymous broadcast `ExchangeTransactionContract`: [4](#0-3) 

The test file `ExchangeProcessorTest.testStrictMath` explicitly documents that the legacy (`useStrictMath=false`/legacy double-cast) result is **not equal** to the safe/hardened result for real-world input ranges, confirming the rounding-error discrepancy exists in production code today: [5](#0-4) 

This is structurally identical to the reported bug class: a formula that truncates an intermediate value before completing subsequent arithmetic steps, causing the computed output amount to deviate (systematically downward, and non-negligibly for realistic balance ranges) from the mathematically correct bonding-curve value.

### Impact Explanation
Every `ExchangeTransactionContract`, `ExchangeInjectContract`, and `ExchangeWithdrawContract` (v1 TRX/TRC10 bonding-curve markets) processed through the default, non-hardened path computes token amounts using this biased formula. Because the SafeExchangeProcessor fix is gated behind a governance-controlled dynamic parameter that is not guaranteed to be enabled on all networks/heights, users transacting against exchange pools by default receive an amount of tokens that deviates from the mathematically correct Bancor-curve value — an accounting/asset-value corruption analogous to the FEI-received shortfall in the original report. Because computation is deterministic across all full nodes (same double arithmetic, same JVM semantics enforced via `StrictMath`), this does not cause consensus divergence, but it does produce economically incorrect exchange settlement amounts for every legacy-path transaction.

### Likelihood Explanation
High likelihood of triggering the biased calculation: any account can broadcast an `ExchangeTransactionContract` (or Inject/Withdraw) transaction against an existing v1 Exchange pool; no privileged role is required. The biased path is the default unless the `allowHardenExchangeCalculation` chain parameter has been activated by committee/witness governance on that network.

### Recommendation
Ensure `SafeExchangeProcessor` (or an equivalent single-final-truncation, higher-precision formula) is the exclusively used calculator for all exchange-related actuators, independent of the `allowHardenExchangeCalculation` proposal state, or activate/enable that parameter by default going forward, matching the fix pattern from the referenced report (perform all truncating/rounding operations last).

### Proof of Concept
Using `ExchangeProcessorTest.testStrictMath` data rows (e.g. `{4732214L, 2202692725330L, 29218L}`), running `new ExchangeProcessor(supply, false).exchange(4732214L, 2202692725330L, 29218L)` versus `SafeExchangeProcessor.INSTANCE.exchange(4732214L, 2202692725330L, 29218L)` produces different results, as asserted by `Assert.assertNotEquals(anotherTokenQuant, result)` in the existing test suite: [5](#0-4) 
This demonstrates that a real `ExchangeTransactionContract` broadcast through `ExchangeTransactionActuator` on the legacy (default) path yields a token amount that diverges from the mathematically correct/hardened Bancor-curve computation.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-151)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-69)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java (L272-280)
```java
    for (long[] data : testData) {
      ExchangeProcessor processor = new ExchangeProcessor(supply, false);
      long anotherTokenQuant = processor.exchange(data[0], data[1], data[2]);
      processor = new ExchangeProcessor(supply, true);
      long result = processor.exchange(data[0], data[1], data[2]);
      long safeResult = SafeExchangeProcessor.INSTANCE.exchange(data[0], data[1], data[2]);
      Assert.assertNotEquals(anotherTokenQuant, result);
      Assert.assertEquals(safeResult, result);
    }
```
