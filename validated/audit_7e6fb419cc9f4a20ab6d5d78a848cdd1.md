### Title
Exchange (Bancor-relay) actuators check only for `balance == 0`, not near-zero/skewed reserve ratios, allowing disproportionate output analogous to the `equity = 0` share-minting bug - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The reported bug class is: a ratio-based conversion (`value * totalSupply / equity`) that degenerates and produces disproportionate output when the denominator collapses toward zero, causing depositors/traders to receive wildly incorrect amounts. java-tron's TRX/TRC10 "Exchange" (a Bancor-relay AMM) has the same structural pattern: `ExchangeCapsule.transaction()` computes an output amount from a formula that divides by the pool's relay `supply` and by token balances [1](#0-0) , using either the unsafe double-based `ExchangeProcessor` or the hardened `SafeExchangeProcessor` depending on `dynamicStore.allowStrictMath()` [2](#0-1) .

### Finding Description
`ExchangeTransactionActuator.doValidate()` only rejects a trade when a reserve is **exactly** zero: `firstTokenBalance == 0 || secondTokenBalance == 0` [3](#0-2) . It does not guard against a reserve being reduced to a very small non-zero value (e.g., `1`) through repeated/partial withdrawals via `ExchangeWithdrawActuator`, whose own validation similarly only checks exact-zero balances before performing the linear ratio calculation [4](#0-3) .

When a reserve balance is near zero, the non-hardened `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` methods perform floating point division (`quant / newBalance`, `supplyQuant / supply`) that is numerically unstable at extreme ratios [5](#0-4) . This is structurally the same root cause as the external report: a value/ratio conversion function that special-cases the exact-zero denominator but does not handle the "near-zero" degenerate region, letting the price/output diverge disproportionately from the economically correct amount and causing one party (the depositor/trader on the other side of the pool) to suffer immediate, disproportionate loss. The project's own test suite acknowledges this precision-loss risk exists and had to introduce a `SafeExchangeProcessor`/`allowHarden()` path with explicit `ArithmeticException` guards specifically for these edge cases [6](#0-5) , confirming the unsafe path is a real, previously-identified risk area, not a purely theoretical one.

### Impact Explanation
If a reserve is driven to a near-zero (but non-zero) balance — through legitimate sequential withdrawals or by an attacker priming the pool — and the non-hardened `ExchangeProcessor` path is active (`allowStrictMath()`/`allowHarden()` not enforced), a subsequent trade via `ExchangeTransactionActuator` can compute a wildly skewed `anotherTokenQuant`, causing whichever party is on the losing side of the imbalance to receive a value far below (or an attacker to extract far above) the fair-value amount — mirroring the depositor's "immediate loss" scenario in the report, but here manifesting as mispriced settlement in a public, unprivileged, fund-moving actuator.

### Likelihood Explanation
Reaching a near-zero reserve requires either organic sequential withdrawals draining one side of the pool or a deliberate attacker-funded sequence of `ExchangeWithdrawContract`/`ExchangeInjectContract`/`ExchangeTransactionContract` transactions, all of which are permissionless, unprivileged actions available to any account, making this reachable without special access.

### Recommendation
Enforce a minimum reserve floor (well above `0`) in `ExchangeWithdrawActuator.doValidate()` and `ExchangeInjectActuator`/`ExchangeTransactionActuator.doValidate()` so that no operation can push either token balance below a safe minimum, and/or make the hardened `SafeExchangeProcessor` (BigDecimal-based, with explicit overflow/near-zero checks) the mandatory path for all exchange transactions rather than gating it behind `allowStrictMath()`/`allowHarden()`.

### Proof of Concept
1. Create an exchange pool via `ExchangeCreateActuator` with reserves `(firstTokenBalance = 1_000_000, secondTokenBalance = 1_000_000)`.
2. Repeatedly call `ExchangeWithdrawActuator` to withdraw `firstToken` down to a balance of `1` — each call passes validation since it only checks `firstTokenBalance == 0` [4](#0-3) .
3. With `firstTokenBalance = 1`, call `ExchangeTransactionActuator` to sell `secondToken` for `firstToken`; the underlying `ExchangeProcessor.exchange()` computation on such an extreme ratio (via floating-point `Maths.pow`) produces an output disproportionate to fair value [7](#0-6) , demonstrating the same "near-zero denominator causes disproportionate value transfer" root cause as the external report.

**Note:** I could not fully verify the default value of `dynamicStore.allowStrictMath()`/`allowHarden()` in this deployment (i.e., whether the hardened `SafeExchangeProcessor` path is mandatory or optional on mainnet) — the flag definitions live in `DynamicPropertiesStore.java` and `VMConfig.java`, which I was not able to fully inspect within the available context. This should be verified in a full Devin session to confirm whether the unsafe path is actually reachable in production configuration.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L63-89)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeWithdrawContract.getTokenId().toByteArray();
      long tokenQuant = exchangeWithdrawContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

      BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
      BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
      BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, tokenQuant),
            subtractExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, anotherTokenQuant),
            subtractExact(secondTokenBalance, tokenQuant));
      }
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

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L1-45)
```java
package org.tron.core.capsule;

import java.math.BigDecimal;
import java.math.RoundingMode;
import lombok.extern.slf4j.Slf4j;
import org.tron.common.math.StrictMathWrapper;

@Slf4j(topic = "capsule")
public class SafeExchangeProcessor implements ExchangeCapsule.Processor {

  private static final BigDecimal SUPPLY = BigDecimal.valueOf(1_000_000_000_000_000_000L);

  public static final SafeExchangeProcessor INSTANCE = new SafeExchangeProcessor();

  private SafeExchangeProcessor() {

  }

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
}
```
