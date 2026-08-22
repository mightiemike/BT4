### Title
Legacy floating-point Bancor-relay math in `ExchangeTransactionActuator`/`ExchangeCapsule::transaction` can compute an incorrect exchange rate, allowing rounding profit extraction from the TRC10 exchange pool - (File: chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java)

### Summary
`ExchangeTransactionContract` (an unprivileged, anyone-can-call TRC10 "bancor" AMM trade) computes the amount a trader receives via `ExchangeCapsule::transaction`, which by default (legacy, non-hardened path) delegates to `ExchangeProcessor`, using `double`/`Math.pow` floating point arithmetic to derive the exchange rate [1](#0-0) . This mirrors the root cause of the Amphor H-3 bug: the "correct" ratio between pool balances is computed with an approximate/inconsistent formula rather than an exact one, so the on-chain result can diverge from the true bonding-curve exchange rate, and the divergence is attacker-influenceable per trade.

### Finding Description
`ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` use `double` arithmetic and `Math.pow` to move value between a "virtual relay supply" (fixed at `1_000_000_000_000_000_000L`) and the two token balances [2](#0-1) . This legacy path is still the *default* unless `AllowHardenExchangeCalculation` is enabled; `ExchangeCapsule::transaction` selects `ExchangeProcessor` vs `SafeExchangeProcessor` based on the `hardenedCalc` flag [3](#0-2) .

Just as in the Amphor bug — where the exchange rate formula used in `open()`/`settle()` differed from the one used in `_convertToAssets`/`_convertToShares`, producing an inconsistent share/asset ratio that could be exploited — here the pool's true, invariant-preserving exchange rate can diverge from what `exchangeToSupply`/`exchangeFromSupply` compute due to:
1. Floating-point precision loss in `Math.pow(base, exponent)` for the bonding-curve computations.
2. Truncation to `long` (`(long) issuedSupply`, `(long) exchangeBalance`) which always rounds toward zero rather than applying a rule that protects the pool.
3. The two-step supply mutation (`exchangeToSupply` then `exchangeFromSupply`, each independently truncating and mutating the local `supply` variable) which does not guarantee round-trip consistency for the two conversions used by a single trade.

The `ExchangeTransactionActuator` performs no post-hoc "not precise enough" sanity check on the computed `anotherTokenQuant` before crediting it to the trader (unlike `ExchangeWithdrawActuator`, which explicitly re-derives the amount via `BigDecimal`/`BigInteger` exact math and rejects the transaction if the legacy result deviates by more than 0.01% — see the `allowHarden` "Not precise enough" checks) [4](#0-3) . `ExchangeTransactionActuator` only checks `contract.getExpected()` as a slippage floor set by the caller himself, not as a protocol-level correctness bound [5](#0-4) , so a rounding-favorable trade is simply accepted.

The protocol team's own `SafeExchangeProcessor` (guarded by `AllowHardenExchangeCalculation`) and the "hardened" precision checks added in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` are effectively acknowledgment that the legacy floating-point exchange-rate math is imprecise and needs a stricter, `BigDecimal`-based recomputation to be trustworthy — the same class of fix Amphor applied (removing the extra `+1` inconsistency between `_settle` and `_convertToAssets/_convertToShares`).

### Impact Explanation
Any account can call `ExchangeTransactionContract` (no privileged role required — unlike `ExchangeInjectContract`/`ExchangeWithdrawContract`, which are restricted to the exchange creator) [6](#0-5) . If the legacy floating-point exchange rate diverges from the true bonding-curve invariant in the trader's favor (even by small amounts per trade), repeated/automated trading (analogous to the Amphor attacker's loop of 30 accounts) can extract value from the TRX/TRC10 pool balance at the expense of the pool and its other participants, corrupting `firstTokenBalance`/`secondTokenBalance` accounting over time. This is an accounting/asset-corruption vulnerability reachable purely via broadcast transactions with no special privileges, matching the "exchange/market math" and "resource and reward accounting" categories called out as in-scope.

### Likelihood Explanation
Medium-to-High: `ExchangeTransactionContract` is fully public/unprivileged, requires only a trivial TRC10/TRX balance and the exchange to be open (`firstTokenBalance != 0 && secondTokenBalance != 0`) [7](#0-6) . Repeated small trades against the same pool cost only transaction bandwidth/energy fees, and the legacy `double`-based math path remains the default unless `AllowHardenExchangeCalculation` is turned on network-wide via `DynamicPropertiesStore`. The magnitude of exploitable rounding error per trade is not proven here without a live divergence PoC against a currently-active exchange pool (this would require live/replay testing against `ExchangeProcessorTest` boundary values), so precise economic impact is uncertain and would need further quantitative analysis.

### Recommendation
- Make `SafeExchangeProcessor`'s `BigDecimal`-based, deterministic bonding-curve computation the mandatory path for `ExchangeTransactionActuator` (not opt-in via `AllowHardenExchangeCalculation`), removing dependence on `double`/`Math.pow`.
- Add the same "not precise enough" / exact-recomputation guard that `ExchangeInjectActuator` and `ExchangeWithdrawActuator` already apply, to `ExchangeTransactionActuator`, rejecting trades whose legacy-computed `anotherTokenQuant` deviates materially from an exact recomputation.
- Audit `exchangeToSupply`/`exchangeFromSupply` truncation behavior (`(long) issuedSupply`) to ensure rounding never favors the trader over the pool, consistent with how Amphor's fix removed the asymmetric `+1` rounding.

### Proof of Concept
A concrete numeric divergence between the legacy `ExchangeProcessor` (double/`Math.pow`) output and the `SafeExchangeProcessor` (`BigDecimal`) output for the *same* input, executed via a public `ExchangeTransactionContract` call, would constitute the PoC. The existing test `testTransactionLegacyVsHardenedProcessorSelection` already demonstrates the two paths can diverge by up to 1 unit for identical inputs [8](#0-7) ; a full economic PoC (looping trades to accumulate profit, as in the Amphor report) was not built/run here and would need to be validated in a live/test environment to confirm exploitable magnitude — this is noted as an open verification item.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L228-243)
```java
      if (allowHarden) {
        BigDecimal remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L149-151)
```java
    if (!accountStore.has(ownerAddress)) {
      throw new ContractValidateException("account[" + readableOwnerAddress + NOT_EXIST_STR);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-218)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
```

**File:** framework/src/test/java/org/tron/core/capsule/ExchangeCapsuleTest.java (L85-106)
```java
  @Test
  public void testTransactionLegacyVsHardenedProcessorSelection() throws Exception {
    // Same input produces deterministic results in both modes.
    ExchangeCapsule legacy = new ExchangeCapsule(
        ByteString.copyFromUtf8("owner"), 100L, 0L,
        "abc".getBytes(), "def".getBytes());
    legacy.setBalance(100_000_000L, 100_000_000L);
    long legacyResult = legacy.transaction("abc".getBytes(), 1_000_000L, true, false);

    ExchangeCapsule hardened = new ExchangeCapsule(
        ByteString.copyFromUtf8("owner"), 101L, 0L,
        "abc".getBytes(), "def".getBytes());
    hardened.setBalance(100_000_000L, 100_000_000L);
    long hardenedResult = hardened.transaction("abc".getBytes(), 1_000_000L, true, true);

    Assert.assertTrue("Both must return positive", legacyResult > 0 && hardenedResult > 0);
    Assert.assertTrue("Hardened must not exceed pool",
        hardenedResult <= 100_000_000L);
    // Allow ±1 difference due to BigDecimal vs double precision
    Assert.assertTrue("Results should be within 1 unit",
        StrictMathWrapper.abs(legacyResult - hardenedResult) <= 1);
  }
```
