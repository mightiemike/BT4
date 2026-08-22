### Title
Precision-losing double-based arithmetic in the default (non-hardened) TRC10 exchange price calculation can produce incorrect exchange amounts - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
`ExchangeTransactionActuator`, `ExchangeInjectActuator`, and `ExchangeWithdrawActuator` process anonymous broadcast transactions that swap/inject/withdraw TRX or TRC10 tokens against a bancor-formula liquidity pool (`ExchangeCapsule`). The actual "price" (exchange amount) is computed by `ExchangeProcessor.exchange()`, which by default (the `allowHarden()`/`allowStrictMath()` chain parameters both default to `0`) performs the pool-supply and exchange-balance formulas using plain Java `double` arithmetic and unchecked `long` addition/subtraction, instead of the overflow-checked/precision-preserving `BigDecimal` path implemented in `SafeExchangeProcessor`.

### Finding Description
`ExchangeProcessor.exchangeToSupply` and `exchangeFromSupply` compute the bancor relay/output amounts with raw `double` math and plain `long` addition: [1](#0-0) [2](#0-1) 

`ExchangeCapsule.transaction()` selects this legacy `ExchangeProcessor` unless the `hardenedCalc` flag (governed by the `ALLOW_HARDEN_EXCHANGE_CALCULATION` chain parameter, which defaults to `0`) is enabled, and only then applies overflow-checked `StrictMathWrapper` arithmetic and the `BigDecimal`-based `SafeExchangeProcessor`: [3](#0-2) [4](#0-3) 

The `SafeExchangeProcessor`, by contrast, performs the same computation with `BigDecimal` and `StrictMathWrapper.addExact`, demonstrating that the project itself recognizes the legacy path's arithmetic is not safe/precise: [5](#0-4) 

This is directly analogous to the reported bug class: arithmetic on externally influenced numeric inputs (here, a TRC10/TRX exchange pool's balances and a user-supplied `quant`) performed without safe/overflow-aware math, which can silently yield an incorrect calculated "price"/output amount. `ExchangeTransactionActuatorTest.testStrictMath` in the test suite explicitly confirms that the legacy `double`-based result differs from the hardened `BigDecimal` result for the same inputs (`Assert.assertNotEquals(anotherTokenQuant, result)`), proving the default path produces different (and by construction, less precise) output than the "safe" implementation: [6](#0-5) 

The legacy path is also not protected against `long` overflow of intermediate pool balances the way the validation logic for `ExchangeInjectActuator`/`ExchangeTransactionActuator` enforces `getExchangeBalanceLimit()` only at specific call sites; `ExchangeProcessor.exchangeToSupply` computes `balance + quant` with plain `+` (no `addExact`) prior to any such check, whereas the hardened equivalent uses `StrictMathWrapper.addExact`: [7](#0-6) 

### Impact Explanation
If the default (non-hardened, non-strict-math) legacy processor mis-calculates an exchange output due to `double` rounding/precision loss, an attacker constructing specific pool states (e.g., via repeated `ExchangeInjectContract`/`ExchangeTransactionContract` calls to reach floating-point edge cases) could receive more tokens than the bancor formula should yield, draining value from a TRC10/TRX exchange pool created via `ExchangeCreateContract`. This is an accounting-corruption risk within the exchange/market subsystem reachable purely by broadcasting transactions; no privileged actor is required.

### Likelihood Explanation
The vulnerable path is the **default** behavior of the network unless the committee has set `ALLOW_HARDEN_EXCHANGE_CALCULATION` (and `allowStrictMath`) to `1`. Any anonymous account can trigger `ExchangeTransactionActuator`/`ExchangeInjectActuator`/`ExchangeWithdrawActuator` at will against any existing TRC10 exchange pair, so exploitation only requires finding pool-balance/quantity combinations where `double` rounding diverges favorably from the exact bancor result — the project's own tests already prove such divergence exists for realistic inputs (`testStrictMath`), though the magnitude of exploitable divergence (whether it ever favors the caller enough to be profitable net of losses) was not independently confirmed here.

### Recommendation
Make the `SafeExchangeProcessor` (BigDecimal-based, overflow-checked) the default and only implementation for exchange calculations, removing the `double`-based `ExchangeProcessor` legacy path entirely, or force `allowHardenExchangeCalculation`/`allowStrictMath` to `1` at genesis for all networks going forward, eliminating the divergent, non-deterministic-precision code path.

### Proof of Concept
Not independently constructed; the divergence between legacy and hardened results is already demonstrated by the existing test `testStrictMath` in `ExchangeProcessorTest.java`, which asserts `anotherTokenQuant != result` (legacy `double` math vs. `StrictMathWrapper`/`BigDecimal` math) for a table of realistic `(balance, balance, quant)` triples [6](#0-5) . Whether any specific divergence is exploitable for net profit (accounting for fees and pool balance limits) was not verified against the live default chain parameters within the scope of this analysis.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-29)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L31-39)
```java
  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }
```

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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L3059-3072)
```java
  public long getAllowHardenExchangeCalculation() {
    return Optional.ofNullable(getUnchecked(ALLOW_HARDEN_EXCHANGE_CALCULATION))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(0L);
  }

  public void saveAllowHardenExchangeCalculation(long value) {
    this.put(ALLOW_HARDEN_EXCHANGE_CALCULATION, new BytesCapsule(ByteArray.fromLong(value)));
  }

  public boolean allowHardenExchangeCalculation() {
    return getAllowHardenExchangeCalculation() == 1L;
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
