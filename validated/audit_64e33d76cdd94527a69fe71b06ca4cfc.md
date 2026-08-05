## Title
Unsafe (non-overflow-checked) arithmetic in the default TRX/TRC10 Exchange (Bancor-relay) calculation path — (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`, `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`, `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java`)

### Summary
java-tron's built-in `Exchange` feature (a Bancor-style relay-token AMM for TRX/TRC10 pairs, reachable by any account via `ExchangeTransactionContract`, `ExchangeInjectContract`, and `ExchangeWithdrawContract`) performs its core pricing math using plain, unchecked `long`/`double` arithmetic by default. This mirrors the RubiconMarket.sol finding: raw multiplication/addition on user-influenced quantities without overflow protection, with a "safe" implementation existing in the codebase but only active when a governance-gated flag is turned on.

### Finding Description
The default (non-hardened) processor `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` computes the Bancor relay conversion using raw `double` multiplication of `supply` and pool `balance` against a user-controlled `quant`, with no overflow or precision-loss checks, then narrows the result via a raw `(long)` cast: [1](#0-0) [2](#0-1) 

`ExchangeCapsule.transaction` selects between this legacy unsafe `ExchangeProcessor` and the safer `SafeExchangeProcessor` (which uses `BigDecimal` and `addExact`/`subtractExact`) purely based on a boolean `hardenedCalc` flag, and when that flag is `false` it also performs the pool-balance updates with plain `+`/`-` instead of exact-checked arithmetic: [3](#0-2) 

The `hardenedCalc` flag is sourced from `AbstractExchangeActuator.allowHarden()`, which reads the dynamic property `allowHardenExchangeCalculation`: [4](#0-3) 

All exchange-related actuators (`ExchangeTransactionActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`) route through this same `allowHarden()`-gated switch, and every test in the codebase that exercises the "hardened"/safe path does so by explicitly calling `dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(1)` before the test, e.g.: [5](#0-4) 
This pattern (test must explicitly turn the flag on to exercise the safe path) indicates that `allowHardenExchangeCalculation` defaults to disabled (0) on a fresh/unmodified chain, meaning the legacy, unchecked-arithmetic `ExchangeProcessor` and plain `+`/`-` update path in `ExchangeCapsule.transaction`/`AbstractExchangeActuator` is the actual default behavior any unprivileged user hits when calling `ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract`, exactly analogous to RubiconMarket.sol's raw, unchecked `*`/`1e18` multiplications that were only fixed by opting into `DSMath.mul`.

### Impact Explanation
Because `exchangeToSupply`/`exchangeFromSupply` operate in double-precision floating point on attacker/user-supplied `sellTokenQuant` against pool `balance`/`supply` values, and the surrounding `ExchangeCapsule.transaction` legacy branch performs unchecked `long` addition/subtraction on pool balances, a sufficiently large or crafted `tokenQuant` (bounded only by `dynamicStore.getExchangeBalanceLimit()`, itself a governance-settable long) can cause loss of precision, incorrect `buyTokenQuant` computation, or unchecked overflow/underflow of `firstTokenBalance`/`secondTokenBalance`. This directly corresponds to the reported impact of "purchasing and selling amounts improperly fulfilled" and "improper tracking" of pool reserves, i.e., incorrect settlement/accounting of the on-chain exchange pool state that other users' trades and validate-time price/slippage checks (`tokenExpected` in `ExchangeTransactionActuator.doValidate`) depend on.

### Likelihood Explanation
This is reachable by any account with no special privileges — simply issuing `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract` transactions against any existing Exchange pool. The unsafe path is the default network behavior unless the SR committee has separately activated the `allowHardenExchangeCalculation` proposal, similar to how the RubiconMarket report's likelihood was tempered by needing `pay_amt` to approach `type(uint256).max / 1e18` — here it requires balances/quantities near `Long.MAX_VALUE` bounds or double-precision edge cases, but the mechanism (raw arithmetic instead of the codebase's own available safe primitives) is confirmed present and default-active.

### Recommendation
Make `hardenedCalc`/`allowHarden()` unconditionally true (or remove the legacy `ExchangeProcessor`/raw-arithmetic branch entirely) so that `ExchangeCapsule.transaction` and `AbstractExchangeActuator.addExact`/`subtractExact` always route through `SafeExchangeProcessor` and `StrictMathWrapper.addExact`/`subtractExact`, consistent with the report's recommendation to always use the safe-math variant (`DSMath.mul` analog) rather than gating it behind an opt-in flag.

### Proof of Concept
1. Deploy/observe an `Exchange` pool with large `firstTokenBalance`/`secondTokenBalance` (up to `dynamicStore.getExchangeBalanceLimit()`).
2. On a chain where `allowHardenExchangeCalculation` has not been enabled (default state, as evidenced by tests needing to explicitly call `saveAllowHardenExchangeCalculation(1)`, e.g. [5](#0-4) ), submit an `ExchangeTransactionContract` with a large `tokenQuant`.
3. `ExchangeTransactionActuator.execute` calls `exchangeCapsule.transaction(tokenID, tokenQuant, allowStrictMath, allowHarden())` with `allowHarden()==false`, routing into `ExchangeCapsule.transaction`'s legacy branch [3](#0-2)  and `ExchangeProcessor`'s unchecked double arithmetic [6](#0-5) , which can compute an incorrect `buyTokenQuant`/mutate pool balances without overflow detection, unlike `SafeExchangeProcessor` which throws `ArithmeticException` on overflow as demonstrated in `framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java` (`testHardenedOverflowDetection`, `testSafeProcessorAddExactOverflowThrows`).

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-158)
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

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1836-1839)
```java
  public void hardenedSuccessExchangeTransaction() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(1);
    dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(1);
    InitExchangeSameTokenNameActive();
```
