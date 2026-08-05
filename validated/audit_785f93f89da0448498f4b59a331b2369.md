### Title
Unvalidated Integer Underflow in Non-Hardened `ExchangeCapsule.transaction()` Pool Balance Update - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`ExchangeCapsule.transaction()` computes the counter-party token amount (`buyTokenQuant`) with the floating-point bancor-style `ExchangeProcessor` and then updates the pool reserves with raw, unchecked `long` arithmetic (`secondTokenBalance - buyTokenQuant` / `firstTokenBalance - buyTokenQuant`) whenever the committee-controlled `allowHardenExchangeCalculation` flag is not enabled. This is the same bug class as the reported `appendChangeTxOut` issue: a subtraction that is not validated for underflow before being persisted as the new authoritative state.

### Finding Description
`ExchangeCapsule.transaction()` selects between two calculation paths based on `hardenedCalc`: [1](#0-0) 

When `hardenedCalc` is `false` (the non-hardened path), `newFirstTokenBalance`/`newSecondTokenBalance` are computed with plain `-`/`+` on `long`, and the guard that rejects negative results only fires `if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0))`: [2](#0-1) 

`hardenedCalc` is only `true` when the actuator's `allowHarden()` returns true, which is gated by the dynamic/proposal parameter `allowHardenExchangeCalculation`: [3](#0-2) 

In the default (non-hardened) path, `buyTokenQuant` itself comes from `ExchangeProcessor.exchange()`, which relies on `double`-precision `Math.pow` computations: [4](#0-3) 

Because this is floating-point math rather than exact arithmetic, `buyTokenQuant` can, for edge-case reserve/quant combinations, be computed slightly larger than the actual counter-party reserve. In the non-hardened path this directly underflows the pool balance (`secondTokenBalance - buyTokenQuant < 0` or `firstTokenBalance - buyTokenQuant < 0`) with no bounds check, and the corrupted (negative) balance is written straight into the persisted `ExchangeCapsule`/store via `ExchangeTransactionActuator.execute()`: [5](#0-4) 

`ExchangeTransactionActuator.doValidate()` only bounds-checks the caller's own account balance and the resulting `tokenBalance` against `exchangeBalanceLimit` on the *sold* side; it never validates that the *bought* side's new balance stays non-negative: [6](#0-5) 

This mirrors the reported bug exactly: a change/settlement value is computed and subtracted from a reserve without verifying sufficiency, and the negative/wrapped result is accepted and persisted as valid on-chain state. The project's own remediation for this exact defect is visible in the codebase: the `hardenedCalc`/`SafeExchangeProcessor` path was added later specifically to add the missing bounds check (`newFirstTokenBalance < 0 || newSecondTokenBalance < 0` and `BigDecimal`-based exact math), confirming the underlying non-hardened path is the vulnerable analog: [7](#0-6) 

### Impact Explanation
Any unprivileged user who has created or can trade against a bancor-based TRC10/TRX exchange pair (`ExchangeCreateContract`/`ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract`) can reach this code path. If precision error at the edge of the bancor curve pushes the computed `buyTokenQuant` to exceed the actual reserve, the exchange pool's `firstTokenBalance`/`secondTokenBalance` becomes negative and is persisted to `ExchangeStore`/`ExchangeV2Store`. Because this value is later reused as an operand in subsequent trades (`processor.exchange(firstTokenBalance, secondTokenBalance, sellTokenQuant)`), this results in an invalid, divergent accounting state for the exchange pool — subsequent trades against the poisoned pool would compute further incorrect amounts, potentially allowing a user to extract tokens beyond what the pool actually holds (accounting/settlement corruption), or causing arithmetic exceptions/halts for later traders once the corrupted negative balance flows into other computations.

### Likelihood Explanation
Likelihood is constrained by two factors: (1) `allowHardenExchangeCalculation` must be disabled (the historical/default state prior to activation, and it remains a per-network committee-toggled parameter so older/forked networks may still run with it off), and (2) triggering an actual underflow requires reserve/quant values landing in a floating-point precision edge case of the bancor formula, which is not guaranteed on every trade but is a known class of error for `double`-based AMM math at extreme ratios or after repeated compounding rounding. This is lower likelihood than the original Bitcoin PoC (which is trivially triggerable), but it is unprivileged-user reachable through standard `ExchangeTransactionContract`/`ExchangeInjectContract` calls with no special permissions.

### Recommendation
Make the bounds-checked, `BigDecimal`-based `SafeExchangeProcessor` path (currently gated behind `allowHardenExchangeCalculation`) the unconditional/default behavior for all exchange pool balance updates, or at minimum add the same `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` guard to the non-hardened branch in `ExchangeCapsule.transaction()` regardless of `hardenedCalc`, throwing `ContractValidateException`/aborting the actuator before any state mutation is persisted.

### Proof of Concept
Conceptual PoC (cannot be executed without live node/DB access):
1. On a network where `allowHardenExchangeCalculation` is not yet enabled, create a bancor exchange pool with `ExchangeCreateContract` using reserve values near the boundary where `ExchangeProcessor.exchangeFromSupply`'s `double`-based `Math.pow` computation loses precision.
2. Submit an `ExchangeTransactionContract` with a `quant` chosen such that the resulting `buyTokenQuant` (computed via floating point) is computed slightly greater than the true counter-reserve.
3. `ExchangeCapsule.transaction()` executes `secondTokenBalance - buyTokenQuant` (or `firstTokenBalance - buyTokenQuant`) unchecked, producing and persisting a negative reserve balance in `ExchangeStore`/`ExchangeV2Store`.
4. Subsequent trades against this pool use the corrupted negative reserve as an operand in `ExchangeProcessor.exchange()`, producing further incorrect settlement amounts.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L147-166)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L64-96)
```java
      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

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

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-221)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L1-44)
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
```
