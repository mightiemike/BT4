[1](#0-0) 

### Title
Floating-point, platform-dependent `Math.pow` arithmetic in the built-in Exchange (Bancor-style AMM) actuators can cause consensus divergence - (File: chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java)

### Summary
The default (non-hardened) code path for TRON's built-in TRC10 exchange (`ExchangeTransactionContract` / `ExchangeInjectContract` / `ExchangeWithdrawContract`) performs its Bancor-formula pricing with IEEE-754 `double` arithmetic, including a call to `Math.pow` that is not guaranteed to be bit-identical across CPU architectures/JVMs. This is precisely the bug class the report warns about ("dangerous… floats have unpredictable small error"), but in a blockchain the consequence is not just rounding noise — it is a potential state/consensus divergence between nodes that compute a different result for the same transaction.

### Finding Description
`ExchangeCapsule.transaction()` selects between two processors depending on the chain parameter `allowHardenExchangeCalculation`: the legacy `ExchangeProcessor` (double-based) or the newer `SafeExchangeProcessor` (BigDecimal-based, gated behind a proposal). [2](#0-1) 

`ExchangeProcessor.exchangeToSupply` / `exchangeFromSupply` compute the Bancor curve entirely in `double`, and call `Maths.pow(base, exponent, useStrictMath)`: [1](#0-0) 

`Maths` itself is documented as deprecated specifically because of cross-platform floating-point inconsistency, and routes to either `StrictMathWrapper` (uses `StrictMath`, deterministic) or `MathWrapper` (uses `Math`, which the JIT/hardware is explicitly permitted to compute with extended precision / FMA instructions on some platforms, i.e. non-deterministic across nodes): [3](#0-2) 

Whether the strict variant is used is itself controlled by another chain parameter (`allowStrictMath` / `useStrictMath`), meaning that until both `allowStrictMath` and `allowHardenExchangeCalculation` proposals are activated network-wide, the exchange-quantity calculation for every `ExchangeTransactionContract` broadcast by any account runs through `Math.pow`, a JVM/platform-dependent double computation, directly inside actuator execution (state transition) logic that must be byte-for-byte identical on every full node to keep consensus.

In addition, `ExchangeWithdrawActuator.doValidate()` still contains a `double`-based "not precise enough" sanity check on the non-hardened path, matching the report's "weird comparison with no guarantee of successful comparison" complaint — the check divides a signed `remainder` by `anotherTokenQuant` without taking an absolute value, so a negative remainder (BigDecimal HALF_UP rounding down relative to floor) can silently pass validation instead of being caught: [4](#0-3) 

### Impact Explanation
If any full node computes `Math.pow` differently than the majority (e.g. due to JIT optimization, CPU FMA usage, or a different JVM/Math library implementation), the resulting `anotherTokenQuant`/`buyTokenQuant` and thus account/exchange balances after executing an `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract` transaction will diverge from other nodes. This causes state root disagreement — a consensus split / chain fork — which is one of the most severe classes of blockchain bugs, and is reachable by any user simply broadcasting a normal exchange transaction (no privileged actor needed).

### Likelihood Explanation
Likelihood is currently mitigated in practice because the codebase already added `StrictMathWrapper`/`allowStrictMath` and a full BigDecimal `SafeExchangeProcessor`/`allowHardenExchangeCalculation` as hardening, indicating the project is aware of this exact risk class. However, exploitability/occurrence depends on whether these proposals have been activated on the live network; until `allowHardenExchangeCalculation` (and ideally `allowStrictMath`) are enabled by committee proposal, the double/`Math.pow` path remains the active, default consensus-critical computation for every exchange trade, so the divergence risk is latent but real given heterogeneous node hardware (x86 vs ARM, differing JIT versions) in a public P2P network.

### Recommendation
- Ensure `allowHardenExchangeCalculation` (routing to `SafeExchangeProcessor`, which uses `BigDecimal`/`StrictMathWrapper`) is activated network-wide, and eventually remove the legacy `double`/`Math.pow`-based `ExchangeProcessor` and `Maths` class entirely rather than keeping them reachable behind a toggle.
- Replace the `double`-based precision check in `ExchangeWithdrawActuator.doValidate()`'s non-hardened branch with the already-implemented `BigDecimal`-based `remainder.compareTo(...)` used in the hardened branch, and use `Math.abs`/`BigDecimal.abs()` semantics so negative remainders are not silently accepted.
- Follow the report's suggested pattern more broadly for any remaining reward/accounting math: replace `x = x * decimal` with integer numerator/denominator multiplication-then-division (as already done in `ExchangeInjectActuator`'s `BigInteger`-based `doValidate`) instead of floating point.

### Proof of Concept
1. On the live/legacy configuration (`allowHardenExchangeCalculation = 0`, default), broadcast an `ExchangeTransactionContract` trading against a TRC10/TRX exchange pool.
2. Actuator execution calls `ExchangeCapsule.transaction(...)` → `new ExchangeProcessor(supply, useStrictMath)` → `exchangeToSupply`/`exchangeFromSupply`, which invoke `Maths.pow(base, exponent, useStrictMath)` [5](#0-4) .
3. If `useStrictMath` is false (default until the corresponding proposal is activated), this resolves to `MathWrapper.pow`, i.e. `Math.pow`, whose result is not guaranteed bit-identical across all JVM/CPU combinations that java-tron full nodes run on.
4. Two nodes computing a different `buyTokenQuant`/`anotherTokenQuant` for the identical transaction will apply different balance updates in `ExchangeCapsule.transaction` (lines 140-158), producing divergent account/exchange states and hence divergent block state roots — a consensus fork.

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-145)
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

**File:** common/src/main/java/org/tron/common/math/Maths.java (L1-19)
```java
package org.tron.common.math;

/**
 * This class is deprecated and should not be used in new code,
 * for cross-platform consistency, please use {@link StrictMathWrapper} instead,
 * especially for floating-point calculations.
 */
@Deprecated
public class Maths {

  /**
   * Returns the value of the first argument raised to the power of the second argument.
   * @param a the base.
   * @param b the exponent.
   * @return the value {@code a}<sup>{@code b}</sup>.
   */
  public static double pow(double a, double b, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.pow(a, b) : MathWrapper.pow(a, b);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L236-243)
```java
      } else {
        double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
      }
```
