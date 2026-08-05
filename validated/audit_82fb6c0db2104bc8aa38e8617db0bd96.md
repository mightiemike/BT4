### Title
Non-hardened TRC10 Exchange bonding-curve math (`ExchangeProcessor`) can overestimate output, producing an invariant-violating "convertToAssets-style" over-return - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The external report flags `_maxYieldVaultWithdraw()` for using an *approximate* conversion function (`convertToAssets`) instead of a *conservative* one (`previewRedeem`), which can make a value-returning function claim more value is available/receivable than is actually redeemable, violating the protocol's rounding invariant. The closest reachable analog in java-tron is the default (non-hardened) TRC10 Exchange bonding-curve implementation, `ExchangeProcessor`, which computes swap outputs with IEEE-754 `double` arithmetic (`Math.pow`) instead of an exact/conservative integer method. Like `convertToAssets`, this is an *approximation* of the true bonding-curve output and can diverge from the "safe" value computed by the exact `SafeExchangeProcessor`, which is only used when the `allowHardenExchangeCalculation` feature is enabled.

### Finding Description
`ExchangeCapsule.transaction()` selects between two `Processor` implementations depending on a feature flag: [1](#0-0) 

- The default path, `ExchangeProcessor`, computes the bonding-curve swap using `double` and `Math.pow`: [2](#0-1) 
- The alternative, `SafeExchangeProcessor`, performs the same bonding-curve math using `BigDecimal` with explicit scale/rounding (`RoundingMode.HALF_UP` intermediate, `RoundingMode.DOWN` at the final step) to floor the result and avoid overestimating the buyer's payout: [3](#0-2) 

Both `ExchangeTransactionActuator.execute()`/`doValidate()` and `ExchangeWithdrawActuator` invoke `exchangeCapsule.transaction(..., allowHarden())`, where `allowHarden()` only returns `true` if the dynamic property `allowHardenExchangeCalculation` has been turned on via a committee proposal: [4](#0-3) [5](#0-4) 

This mirrors the report's bug class precisely: the codebase already contains an *approximate* calculator (`ExchangeProcessor`, using floating-point `pow`) alongside an *exact/conservative* calculator (`SafeExchangeProcessor`, using `BigDecimal` with deterministic rounding), and the approximate one is the one actually reachable by default unless a governance flag is flipped. Floating-point `pow` on the bonding curve `1_000_000_000_000_000_000` base value is exactly the kind of operation that is not guaranteed to round consistently downward — it can output a slightly larger `buyTokenQuant`/`anotherTokenQuant` than the exact integer math would allow, i.e., it can hand a trader more tokens out of the pool than the invariant supports, or leave the two pool balances (`firstTokenBalance`/`secondTokenBalance`) in a state inconsistent with the true constant-relationship curve.

### Impact Explanation
If `allowHardenExchangeCalculation` is not enabled (its default/pre-proposal state), every `ExchangeTransactionContract` trade through `ExchangeTransactionActuator` and every `ExchangeWithdrawContract` price ratio computed non-hardened uses the floating-point `ExchangeProcessor`. An attacker who can influence the pool balances/quant (any unprivileged trader — the exchange functions are open to any account that owns the traded TRC10) can potentially extract slightly more value than the AMM invariant should allow on a given swap, gradually draining the exchange pool of value below what later liquidity providers/withdrawers are entitled to. This is an accounting/invariant-violation impact — it directly parallels the original finding's characterization ("returns too much," "violates the standard") but manifests here as pool-balance drift rather than an EIP-4626 violation. It does not require any privileged role.

### Likelihood Explanation
Likelihood is moderate: the vulnerability requires that the network has not yet activated `allowHardenExchangeCalculation` (the presence of `SafeExchangeProcessor` and the proposal flag strongly suggests the java-tron team already identified float-based rounding risk in the AMM math and added a mitigation gated by governance, exactly as PoolTogether's team did for the original finding). On any deployment where the hardening proposal hasn't been passed, the imprecision is present on every single swap transaction, giving high reachability, though the magnitude of value skew per trade may be small and bounded by `double` precision near the computed powers.

### Recommendation
Make the exact/conservative `SafeExchangeProcessor` (BigDecimal-based, floor-rounded) the default and unconditional Exchange calculator rather than gating it behind `allowHardenExchangeCalculation`, or otherwise ensure the floating-point path can never return a value the pool cannot back. At minimum, verify with a full audit of chain state whether this proposal has been activated on the target networks, since running with `ExchangeProcessor` (double math) leaves swap outputs approximate rather than exact/rounded-down.

### Proof of Concept
Not independently reproduced in this analysis; the finding is derived from static comparison of `ExchangeProcessor` (float-based `exchange()`) versus `SafeExchangeProcessor` (BigDecimal-based `exchange()` with explicit `RoundingMode.DOWN`) reachable via `ExchangeCapsule.transaction()` and gated by `AbstractExchangeActuator.allowHarden()`. A concrete PoC would require instantiating an `Exchange` with specific pool balances and demonstrating a swap through `ExchangeTransactionActuator` where the double-based `ExchangeProcessor.exchange()` output differs (higher) from `SafeExchangeProcessor.exchange()` output for identical inputs — this delta represents value extracted beyond what the exact invariant supports. I was not able to run this computation to confirm concrete magnitude, so the exact bound of the discrepancy is unverified.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-129)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

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

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L67-69)
```java
      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```
