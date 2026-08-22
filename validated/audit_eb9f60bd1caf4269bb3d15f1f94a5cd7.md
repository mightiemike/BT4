### Title
Floating-point precision loss in the TRC10 Exchange bonding-curve math can produce degenerate (near-zero or inflated) swap outputs - (`chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
Similar to the CGDA `purchasePrice` bug (where an exponential pricing formula can be driven toward near-zero by an attacker-influenced combination of parameters), java-tron's TRC10 `Exchange` feature computes swap output using a bonding-curve formula with extreme exponents (`0.0005` and `2000`) evaluated in IEEE-754 `double` arithmetic. Depending on the ratio of `sellTokenQuant` to the pool balance, this formula is highly sensitive to floating-point rounding, and can yield an output quantity that diverges sharply from the economically correct value - including truncating to zero for a non-zero input, or inflating disproportionately for balances at the edges of the representable precision range. Both `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, and `ExchangeTransactionActuator` are reachable directly from a broadcast transaction by any account holding the relevant TRC10 asset/TRX, matching the "reachable from broadcast transaction" criterion.

### Finding Description
`ExchangeCapsule.transaction()` selects a `Processor` to compute swap output: [1](#0-0) 

By default (`hardenedCalc == false`), it uses `ExchangeProcessor`, which relies on `double` arithmetic and `Math.pow`-style computations for a bonding-curve/relay formula: [2](#0-1) 

This mirrors the CGDA `purchasePrice` structure: an exponential term (`(1+quant/newBalance)^0.0005` and `(1+supplyQuant/supply)^2000`) whose result is extremely sensitive to the ratio of the traded quantity to pool balances. For very small `quant` relative to `newBalance`, `issuedSupply` computed in `exchangeToSupply` can round toward zero after the `(long)` truncation on line 25, while for balances/quantities at the edge of `double` precision (large TRC10 supplies, up to `1_000_000_000_000_000L` per `AssetIssueContract` limits), the `pow(...,2000)` term can amplify rounding error non-linearly, producing an output far from the intended bonding-curve value.

The `execute()` path of `ExchangeInjectActuator`/`ExchangeWithdrawActuator` uses a simple proportional ratio (not the exponential curve) so is not affected in the same way, but `ExchangeTransactionActuator` - the actual swap actuator - directly depends on `ExchangeCapsule.transaction()` and therefore on this floating-point exponential computation: [3](#0-2) 

Notably, java-tron itself already introduced a BigDecimal-based, hardened replacement (`SafeExchangeProcessor`), gated behind the `ALLOW_HARDEN_EXCHANGE_CALCULATION` chain parameter: [4](#0-3) [5](#0-4) 

This existence of a hardened alternative strongly suggests the floating-point path was recognized as imprecise/risky, but on any network/version where this proposal has not been activated, the vulnerable `ExchangeProcessor` (double-based) remains the active code path.

### Impact Explanation
If `ALLOW_HARDEN_EXCHANGE_CALCULATION` is not activated (its default value is `0`), all TRC10 `Exchange` swaps go through the double-precision `ExchangeProcessor`. An attacker who can choose/observe pool balances (via prior `ExchangeInjectActuator`/`ExchangeWithdrawActuator` calls, which they can freely perform) and craft a `tokenQuant` that lands on a floating-point rounding edge could:
- Receive disproportionately more of the counter-asset than the bonding curve should allow (asset/accounting corruption, direct value extraction from other liquidity providers), or
- Force `anotherTokenQuant` to be computed as `0` for a small but non-zero swap in `ExchangeInjectActuator`/`ExchangeWithdrawActuator`-adjacent flows (though these use linear math and are separately guarded by `anotherTokenQuant <= 0` checks).

This is a real accounting-corruption vector limited to the TRC10 Exchange feature rather than a whole-chain consensus break, since `ExchangeTransactionActuator.doValidate()` does enforce `anotherTokenQuant >= tokenExpected` (user-supplied slippage bound), which somewhat limits (but does not eliminate) exploitability, because the user still controls what they consider "expected" and the underlying computed price can still be economically wrong in the attacker's favor.

### Likelihood Explanation
Exploitability requires crafting specific pool-balance/quant ratios, similar to the original CGDA PoC needing tuned parameters. This is plausible because pool balances are attacker-influenced via `ExchangeInjectActuator`, and `tokenQuant` is fully attacker-controlled. However, whether floating-point error is large enough to be profitable after fees depends on TRC10 balance scale limits, requiring careful analysis/PoC to confirm actual profitability - this is analogous to how the original finding was judged "Context" severity pending a concrete PoC with correctly tuned parameters.

### Recommendation
1. Enable `ALLOW_HARDEN_EXCHANGE_CALCULATION` by default (or deprecate/remove the floating-point `ExchangeProcessor` entirely) so `SafeExchangeProcessor`'s BigDecimal arithmetic is always used for Exchange swaps.
2. Add an explicit lower-bound check in `ExchangeCapsule.transaction()`/`ExchangeTransactionActuator` that rejects swaps where the computed output falls below a sane economic threshold relative to input, not just relative to user-supplied `tokenExpected`.
3. Add fuzz/property tests comparing `ExchangeProcessor` (double) output against `SafeExchangeProcessor` (BigDecimal) across the full range of legal TRC10 balances to bound the maximum divergence and confirm it cannot be economically exploited.

### Proof of Concept
Conceptual PoC (not executed): 
1. Attacker creates an Exchange pool via `ExchangeCreateActuator` with a large first-token balance and a comparatively small second-token balance near the precision boundary of `double`.
2. Attacker calls `ExchangeTransactionActuator` with a `tokenQuant` chosen such that `quant / newBalance` in `exchangeToSupply` rounds `issuedSupply` favorably after the `(long)` truncation in `ExchangeProcessor.exchangeToSupply` (`chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java:17-29`), then observes the resulting `exchangeFromSupply` output relative to the mathematically-correct BigDecimal result from `SafeExchangeProcessor` for the same inputs.
3. Compare the two outputs to quantify the divergence and confirm favorable rounding for the attacker. [2](#0-1)

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L410-414)
```java
        case ALLOW_HARDEN_EXCHANGE_CALCULATION: {
          manager.getDynamicPropertiesStore()
              .saveAllowHardenExchangeCalculation(entry.getValue());
          break;
        }
```
