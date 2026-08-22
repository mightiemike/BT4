## Title
DoS on `ExchangeWithdrawContract` execution due to an overly tight, unconditional precision-tolerance check in `ExchangeWithdrawActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
When the `AllowHardenExchangeCalculation` proposal is enabled, `ExchangeWithdrawActuator.doValidate()` rejects an otherwise valid `ExchangeWithdrawContract` transaction whenever a recomputed "remainder" exceeds a fixed 0.01% (`0.0001`) tolerance of the computed `anotherTokenQuant`. Because the withdrawal amount is first computed by truncation (`divideToIntegralValue`, i.e. floor), the discarded fractional remainder can be almost a full unit (up to just under 1), while the allowed tolerance scales with `anotherTokenQuant * 0.0001`. For any `anotherTokenQuant` below ~10,000 (in the token's smallest unit), the fixed floor-truncation remainder can exceed the scaled tolerance purely due to normal integer division rounding — not due to any real precision/manipulation problem — causing legitimate, small withdrawals to permanently fail with `"Not precise enough"`.

### Finding Description
`ExchangeCapsule`/`ExchangeWithdrawActuator` compute the counter-token amount for a withdrawal using integer-truncating division: [1](#0-0) 

then, only when hardened math is enabled, recompute the same ratio with `BigDecimal` division rounded to 4 decimal places and compare the difference against a fixed 0.01% tolerance of the truncated result: [2](#0-1) 

The same pattern is repeated for the opposite token direction: [3](#0-2) 

Because `anotherTokenQuant` is obtained via `divideToIntegralValue` (floor), the true fractional part discarded during truncation can be arbitrarily close to `1` unit. The "remainder" check re-derives that same fraction (rounded to 4 decimals) and compares it against `anotherTokenQuant * 0.0001`. This tolerance is proportional to the withdrawal amount, but the discarded fractional part is bounded by `1` regardless of the withdrawal size. Consequently, whenever `anotherTokenQuant * 0.0001 < remainder` — which is essentially guaranteed whenever `anotherTokenQuant` is small (well under 10,000 units) and the true ratio isn't an exact integer — the transaction is rejected with `ContractValidateException("Not precise enough")` even though no real double-spend/precision-abuse condition exists. This is directly analogous to the reported bug class: a hardcoded tolerance/threshold that is calibrated without accounting for the actual mechanical rounding error inherent to the calculation, making a core protocol operation (there: `pump` swap; here: `ExchangeWithdrawContract` execution) fail under realistic, non-adversarial conditions.

The `allowHarden()` gate is controlled by the `AllowHardenExchangeCalculation` dynamic property, settable via governance proposal: [4](#0-3) 

Once this proposal is active network-wide, any exchange creator attempting small withdrawals from a bancor-style exchange pair can be perpetually blocked, since the failure condition is deterministic given the pool balances and quantity — not something the caller can work around by resubmitting.

### Impact Explanation
This is a DoS of a core actuator (`ExchangeWithdrawContract`), reachable from any broadcast transaction once the `AllowHardenExchangeCalculation` feature is toggled on-chain. Exchange creators attempting legitimate small withdrawals are unable to withdraw their tokens/TRX from the bancor exchange pool, effectively freezing funds for that operation path.

### Likelihood Explanation
Likelihood is medium: it requires the `AllowHardenExchangeCalculation` proposal to be active (a real, supported chain configuration path already exercised in the test suite, e.g. `hardenedSuccessExchangeTransaction`), and it is triggered any time an exchange withdrawal's counter-token amount is small relative to pool balances — a common, unprivileged scenario for legacy Bancor-relay exchange pairs still present in java-tron.

### Recommendation
Make the "Not precise enough" tolerance account for the actual bound of the truncation error (at most ~1 unit) rather than a fixed percentage of the (potentially small) output amount — e.g. use an absolute+relative tolerance (`max(1, anotherTokenQuant * epsilon)`), or avoid the redundant re-derivation/rounding check altogether since `divideToIntegralValue` already yields a deterministic, correct floor value that matches the legacy (non-hardened) exchange semantics.

### Proof of Concept
1. Enable `AllowHardenExchangeCalculation` via governance proposal.
2. Create/withdraw from an exchange pair where `secondTokenBalance * tokenQuant / firstTokenBalance` yields a small `anotherTokenQuant` (e.g. under a few thousand units) with a non-integer true ratio.
3. Submit a valid `ExchangeWithdrawContract` with that `tokenQuant`.
4. `doValidate()` computes `anotherTokenQuant` via floor division, then recomputes the 4-decimal rounded remainder; because `anotherTokenQuant * 0.0001` is smaller than the possible floor remainder (up to ~1), the transaction is rejected with `"Not precise enough"` even though the computed `anotherTokenQuant` is correct and matches the non-hardened calculation path.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-227)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L256-271)
```java
      if (allowHarden) {
        BigDecimal remainder = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L1-30)
```java
package org.tron.core.actuator;

import com.google.protobuf.GeneratedMessageV3;
import org.tron.common.math.StrictMathWrapper;
import org.tron.protos.Protocol.Transaction.Contract.ContractType;

public abstract class AbstractExchangeActuator extends AbstractActuator {

  public AbstractExchangeActuator(ContractType type, Class<? extends GeneratedMessageV3> clazz) {
    super(type, clazz);
  }

  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }

  public long subtractExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.subtractExact(x, y) : x - y;
  }

  public long addExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
  }
}


```
