### Title
Unchecked `long` overflow in legacy (non-hardened) exchange arithmetic corrupts TRC10 exchange pool balances and swap outputs - ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
The report's bug class is a silent numeric truncation/overflow when converting a value that exceeds a signed type's range, causing the resulting amount to be wrapped to an incorrect (much smaller or negative) value that is then used in an asset transfer/settlement. The equivalent primitive in java-tron's bancor-style TRC10 exchange (`ExchangeCreate`/`ExchangeInject`/`ExchangeTransaction`/`ExchangeWithdraw`) is plain Java `long` arithmetic performed without overflow checking whenever the "harden" flag is off, which is the historic/default code path.

### Finding Description
`ExchangeCapsule.transaction()` computes new pool balances using either checked arithmetic (`StrictMathWrapper.addExact`/`subtractExact`) when `hardenedCalc` is `true`, or plain `+`/`-` when it is `false`: [1](#0-0) 

The `hardenedCalc` flag is derived from `dynamicStore.allowHardenExchangeCalculation()`/`allowHarden()` in `AbstractExchangeActuator`, whose helper methods `addExact`/`subtractExact` fall back to raw, unchecked `x + y` / `x - y` when the flag is not enabled: [2](#0-1) 

These unchecked adds/subtracts are used throughout the exchange actuators to update both the pool balances and the user's TRX/asset balances, e.g. in `ExchangeInjectActuator.execute()`: [3](#0-2) 

and in `ExchangeTransactionActuator.execute()`: [4](#0-3) 

In addition, the non-hardened `ExchangeProcessor` (used for the actual bancor-formula swap-amount calculation) performs its core math in `double`/`long` without any overflow or range validation and simply truncates the double result to `long` via a narrowing cast: [5](#0-4) 

This mirrors the reported bug class precisely: a numeric value that legitimately exceeds the signed range of the underlying primitive is silently wrapped/truncated during a type-narrowing operation, and the corrupted (magnitude-reduced or sign-flipped) value is then propagated into the settlement path — here, into `exchange.setFirstTokenBalance`/`setSecondTokenBalance` and into the account balance/asset updates that get persisted to state, exactly as the 1inch report's cast propagated a corrupted amount into the `UniswapV3Pool` call.

The existence of `SafeExchangeProcessor` (BigDecimal-based, `addExact`-guarded) and the `allowHardenExchangeCalculation` dynamic property confirms that java-tron maintainers already identified and hardened against this exact overflow class, but the hardened path is opt-in and gated by a chain parameter (verified by proposal/test coverage in `ExchangeProcessorTest`, `ExchangeCapsuleTest`, and the various `*ActuatorTest` "hardened" tests): [6](#0-5) [7](#0-6) 

I was unable to confirm from the indexed contents of `DynamicPropertiesStore.java` what the compiled/activated default value of `allowHardenExchangeCalculation()` is on the currently running mainnet (the relevant getter/setter definitions were not returned by the index, likely due to index size limits). This is material to the actual exploitability today: if the hardened calculation is already activated network-wide via a passed proposal, the unchecked path is dead code for current callers; if it is not yet activated, the legacy unchecked path is the live behavior for every `Exchange*` transaction.

### Impact Explanation
If the legacy (non-hardened) path is active, an attacker who can construct or manipulate exchange pool balances/quantities such that a `+`/`-`/`double→long` operation exceeds `Long.MAX_VALUE`/`Long.MIN_VALUE` or double precision would cause the resulting pool balance or swap output to wrap to a small/negative number. Because the corrupted value is written directly into the persisted `Exchange`/`ExchangeV2` state and into account TRX/TRC10 balances, this is a direct accounting/settlement corruption in an unprivileged, user-reachable public actuator (`ExchangeInjectContract`, `ExchangeWithdrawContract`, `ExchangeTransactionContract`), not a theoretical or internal-only path.

### Likelihood Explanation
Exploitability depends entirely on whether `allowHardenExchangeCalculation` is currently enabled on the target network — this could not be confirmed due to index limitations on `DynamicPropertiesStore.java`. If disabled/not-yet-activated, likelihood is high given TRC10 balances and quantities are attacker/user controlled `long` values reachable by any account issuing exchange contracts. If already enabled, the unchecked branches are unreachable and likelihood is effectively none.

### Recommendation
Confirm the current activation state of `allowHardenExchangeCalculation` on the deployed network. If it is not universally active, either force the hardened (`SafeExchangeProcessor`/`StrictMathWrapper`) path unconditionally for all `Exchange*` actuators, or activate the corresponding chain parameter, removing the unchecked `x + y`/`x - y` fallback in `AbstractExchangeActuator` and the raw `double`→`long` cast in `ExchangeProcessor`.

### Proof of Concept
Not independently reproducible from the indexed context alone — the existing repository tests already demonstrate the mechanism (`ChargeTest.testOverflow`/`testNegative` for TVM-level signed/unsigned truncation, and `ExchangeProcessorTest.testHardenedOverflowDetection`/`ExchangeCapsuleTest.testHardenedTransactionNegativeBalanceThrows` for the exchange-pool overflow), which show that `SafeExchangeProcessor.INSTANCE.exchange(Long.MAX_VALUE, 1_000_000L, 1L)` throws `ArithmeticException` under the hardened path, implying the equivalent unhardened call (`new ExchangeProcessor(...).exchange(Long.MAX_VALUE, 1_000_000L, 1L)` or `ExchangeCapsule.transaction(..., hardenedCalc=false)`) would instead silently overflow/truncate and return a corrupted value: [8](#0-7) [9](#0-8)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L140-157)
```java
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

```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-23)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }

  public long subtractExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.subtractExact(x, y) : x - y;
  }

  public long addExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L73-99)
```java
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .reduceAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L77-91)
```java
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

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L19-44)
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

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    BigDecimal relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java (L159-199)
```java
  @Test
  public void testHardenedOverflowDetection() {
    assertThrows(ArithmeticException.class, () ->
        SafeExchangeProcessor.INSTANCE.exchange(Long.MAX_VALUE, 1_000_000L, 1L));
  }

  @Test
  public void testHardenedSmallQuant() {

    long sellBalance = 1_000_000_000_000_000L;
    long buyBalance = 1_000_000_000_000_000L;
    long sellQuant = 1L;

    long result = SafeExchangeProcessor.INSTANCE.exchange(sellBalance, buyBalance, sellQuant);
    Assert.assertTrue("Result must be non-negative for small quant", result >= 0);
  }

  @Test
  public void testHardenedLargeQuant() {
    long sellBalance = 1_000_000_000_000L;
    long buyBalance = 1_000_000_000_000L;
    long sellQuant = 1_000_000_000_000L; // 100% of sell balance

    long result = SafeExchangeProcessor.INSTANCE.exchange(sellBalance, buyBalance, sellQuant);
    Assert.assertTrue("Result must be positive for large quant", result > 0);
    Assert.assertTrue("Result must be less than buy balance", result < buyBalance);
  }

  @Test
  public void testSafeProcessorDivByZeroThrows() {
    // newBalance = balance + quant = -1 + 1 = 0 -> BigDecimal divide by zero
    assertThrows(ArithmeticException.class,
        () -> SafeExchangeProcessor.INSTANCE.exchange(-1L, 100L, 1L));
  }

  @Test
  public void testSafeProcessorAddExactOverflowThrows() {
    // balance + quant = MAX + 1 -> addExact overflow
    assertThrows(ArithmeticException.class,
        () -> SafeExchangeProcessor.INSTANCE.exchange(Long.MAX_VALUE, 1L, 1L));
  }
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
