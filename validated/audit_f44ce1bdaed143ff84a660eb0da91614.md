### Title
Unchecked negative pool balance in `ExchangeCapsule.transaction()` legacy path can permanently brick a TRC10 Exchange market - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`ExchangeCapsule.transaction()` computes a Bancor-style bonding-curve swap and updates both token balances of an Exchange pool. The result is only checked for a negative post-trade balance when the `hardenedCalc` flag is `true`; the default/legacy path stores whatever the double-precision math produces, even if a pool balance goes negative [1](#0-0) . This is the same bug class as the reported Float Capital issue: a signed accounting value (`poolValue` / pool token balance) can be driven negative by normal (non-privileged) operations, and the code that consumes it downstream has no safe-guard for that state, leading to a permanently malfunctioning market.

### Finding Description
`ExchangeCapsule.transaction()` selects between `ExchangeProcessor` (legacy, `double`-based Bancor formula) and `SafeExchangeProcessor` (BigDecimal-based) depending on the `hardenedCalc` argument, which is only `true` when `allowHarden()` (i.e., `DynamicPropertiesStore.allowHardenExchangeCalculation()`) is enabled [2](#0-1) . After the buy/sell quantities are computed, the guard that rejects a negative resulting balance is gated by `hardenedCalc`:

```
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
``` [3](#0-2) 

When `hardenedCalc` is `false` (the legacy path, reachable whenever the harden proposal has not been activated, or on any pre-existing pool state produced before activation), the computed `newFirstTokenBalance`/`newSecondTokenBalance` is written directly into the protobuf `long` fields with no lower-bound check [4](#0-3) . The legacy `ExchangeProcessor` uses `double` arithmetic and `Math.pow`, and casts the fractional bonding-curve output directly to `long`:
```
double issuedSupply = -supply * (1.0 - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, ...));
long out = (long) issuedSupply;
``` [5](#0-4) 
```
double exchangeBalance = balance * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, ...) - 1.0);
return (long) exchangeBalance;
``` [6](#0-5) 

Because this is `double` precision, at extreme ratios/quantities the returned `buyTokenQuant` can equal or exceed the opposing pool's balance (rounding drift in `Math.pow`), producing `secondTokenBalance - buyTokenQuant < 0` (or vice versa) without the hardened check catching it. This negative balance is committed to the store via `Commons.putExchangeCapsule(...)` in `ExchangeTransactionActuator.execute()` [7](#0-6) .

Crucially, the only sanity check performed by the actuator's `doValidate()` before allowing a trade is `firstTokenBalance == 0 || secondTokenBalance == 0` — it does not check for a negative balance [8](#0-7) . Once a pool balance is negative, every subsequent `exchangeToSupply`/`exchangeFromSupply` call divides/exponentiates using that negative `long` cast to `double`, producing `NaN`/`Infinity` results that get silently truncated by `(long)` casts to `0` or garbage values, exactly analogous to the Float Capital report where an out-of-range signed value corrupts downstream unsigned-style accounting and either reverts or corrupts the market. Here the failure mode is corruption/DoS rather than a Solidity-style revert (Java has no unsigned integer types), but the underlying flaw — a signed accounting balance permitted to go negative without a guard, then consumed by formulas assuming non-negative state — is the same root cause class.

### Impact Explanation
Any unprivileged user calling `ExchangeTransactionContract` (`ExchangeTransactionActuator`) or `ExchangeInjectContract`/`ExchangeWithdrawContract` in legacy (non-hardened) mode can, given the right extreme quantities/ratios, drive one side of a TRC10 exchange pool negative. Once corrupted:
- Subsequent trades against that pool use `Math.pow` on a negative base with a fractional exponent, yielding `NaN`, which truncates to `0` on cast to `long`, meaning `buyTokenQuant`/`anotherTokenQuant` becomes `0` forever for that pool.
- `doValidate()`'s `tokenExpected > 0` requirement combined with `anotherTokenQuant < tokenExpected` then permanently rejects every future trade on that exchange, effectively bricking the market for all users — a permanent denial of service for that TRC10 exchange pair, matching the reported "malfunction of the whole market" impact.
- The corrupted negative balance is also persisted on-chain, so the invalid state is not self-healing.

### Likelihood Explanation
This requires no special privilege — any account can submit `ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract` transactions. The trigger condition depends on floating-point rounding at extreme pool ratios/quantities, which is a narrower trigger window than the original Solidity report (funding+valueChange combined loss), but the code path is structurally identical: the negative-balance guard exists (proving the team recognized the risk) but is explicitly disabled unless `allowHardenExchangeCalculation` is active, so any exchange operating in legacy mode — including all pre-existing pools whose state was written before the harden proposal activation — is exposed.

### Recommendation
- Make the `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` check in `ExchangeCapsule.transaction()` unconditional (remove the `hardenedCalc &&` gate) so both legacy and hardened paths reject a trade that would drive a pool balance negative [9](#0-8) .
- Add an explicit `firstTokenBalance < 0 || secondTokenBalance < 0` validation check in `ExchangeTransactionActuator.doValidate()` (and the corresponding checks in `ExchangeInjectActuator`/`ExchangeWithdrawActuator`) alongside the existing `== 0` check, so any already-corrupted pool is permanently and safely closed rather than left in a state that silently returns `0` for all trades [8](#0-7) .
- Consider deprecating the `double`-based `ExchangeProcessor` entirely in favor of the `BigDecimal`-based `SafeExchangeProcessor` for all exchanges, independent of the `allowHardenExchangeCalculation` proposal switch.

### Proof of Concept
Conceptual reproduction (cannot be executed without full chain state):
1. Ensure `allowHardenExchangeCalculation` proposal is inactive (legacy `ExchangeProcessor` path, `hardenedCalc=false`).
2. Create a TRC10 exchange with a highly skewed ratio between `firstTokenBalance` and `secondTokenBalance` (e.g., one side extremely small relative to the other, within the `EXCHANGE_BALANCE_LIMIT`).
3. Submit an `ExchangeTransactionContract` with `tokenQuant` chosen so that `exchangeFromSupply()`'s `double` computation (`balance * (Math.pow(1 + supplyQuant/supply, 2000) - 1)`) rounds up to a value equal to or exceeding the opposing pool's balance, per the formula in `ExchangeProcessor.exchangeFromSupply()` [6](#0-5) .
4. Because `hardenedCalc=false`, the check at `ExchangeCapsule.java:160` is skipped, and the resulting negative balance is persisted via `Commons.putExchangeCapsule` [10](#0-9) .
5. Any further trade against this exchange calls `exchangeToSupply`/`exchangeFromSupply` with a negative `balance`, producing `NaN`/`Infinity` from `Math.pow`, truncating to `0` or an invalid `long`, permanently breaking the market for that token pair.

Note: I was unable to execute this against a live node/testnet from this environment, so the exact numeric inputs required to trigger the floating-point rounding drift in step 3 are not empirically confirmed here; a Devin session with test execution capability would be needed to construct a concrete failing test case (similar in style to the existing `ExchangeProcessorTest`/`ExchangeCapsuleTest` hardened-mode negative-balance tests already present in the repo) [11](#0-10) .

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L93-96)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
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
