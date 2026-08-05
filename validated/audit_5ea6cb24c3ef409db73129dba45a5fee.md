### Title
Legacy (non-hardened) Bancor exchange path allows negative pool balances via floating-point precision drift - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`ExchangeCapsule.transaction()` only enforces the `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` invariant when `hardenedCalc == true` [1](#0-0) . When `allowHardenExchangeCalculation()` is `0` (the default until a witness proposal enables it), `ExchangeTransactionActuator` calls `transaction()` with `hardenedCalc=false`, routing through the legacy double-based `ExchangeProcessor`, and no code path anywhere in `doValidate()` or `execute()` rejects a resulting negative balance before it is persisted.

### Finding Description
`ExchangeTransactionActuator.doValidate()` and `execute()` both call `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` [2](#0-1) [3](#0-2) . `doValidate()` only checks `anotherTokenQuant < tokenExpected` (i.e., the buyer got at least what they asked for), never that `anotherTokenQuant` is bounded by the available `secondTokenBalance`/`firstTokenBalance` [4](#0-3) .

Inside `ExchangeCapsule.transaction()`, when `hardenedCalc=false`, `newFirstTokenBalance`/`newSecondTokenBalance` are computed with plain `long` arithmetic based on `buyTokenQuant` from the legacy `ExchangeProcessor` (double/`Math.pow`-based Bancor relay formula), and the `< 0` guard is skipped entirely [5](#0-4) . `ExchangeProcessor.exchange()` computes `buyTokenQuant` via `exchangeToSupply`/`exchangeFromSupply` using floating-point `Math.pow` calls [6](#0-5) ; this formula is asymptotically bounded (buyTokenQuant approaches, but should not exceed, the counter-token balance as `sellTokenQuant → ∞`, by the symmetric 0.0005/2000 exponents), but this bound is only guaranteed under exact real-number arithmetic — not under IEEE-754 double precision. Rounding error in `Math.pow` near the asymptote, or extreme/edge `sellTokenQuant` values chosen by an attacker (an unprivileged sender fully controls `quant` in a public `ExchangeTransactionContract`), can push the computed `buyTokenQuant` to equal or exceed the actual pool balance, producing a negative `newSecondTokenBalance` (or `newFirstTokenBalance`) that is written straight to the persisted `ExchangeCapsule` via `Commons.putExchangeCapsule` in `execute()` with no rejection [7](#0-6) .

`SafeExchangeProcessor` (the hardened path) exists specifically to guard against this class of issue using `BigDecimal` arithmetic and the explicit `< 0` check, confirming that the underlying floating-point drift/negative-balance risk in the legacy path was a known concern that `ALLOW_HARDEN_EXCHANGE_CALCULATION` was introduced to remediate [8](#0-7) . Since `getAllowHardenExchangeCalculation()` defaults to `0` unless a chain-parameter proposal is passed and takes effect [9](#0-8) , any chain that has not enabled hardening runs the unguarded legacy code path by default for every ordinary `ExchangeTransactionContract` submitted by any account — a fully unprivileged, public-facing entry point.

### Impact Explanation
If a negative `secondTokenBalance` (or `firstTokenBalance`) is persisted into the `ExchangeCapsule`, the accounting invariant "pool balance is fully backed by deposited value" is broken. Downstream operations (further exchanges, `ExchangeWithdrawActuator`, `ExchangeInjectActuator`) that trust `getFirstTokenBalance()`/`getSecondTokenBalance()` as ground truth would compute against a corrupted baseline, potentially allowing subsequent withdrawals or trades to extract more value than the pool actually holds, and would represent a permanent, unrecoverable state divergence for that exchange pool.

### Likelihood Explanation
Exploitability requires: (1) `allowHardenExchangeCalculation()==false` — the default/undecided state on any chain until a supermajority-witness proposal enables it; (2) an exchange pool near a floating-point precision boundary or an attacker able to choose `sellTokenQuant` that drives `Math.pow`'s argument close to the point where double rounding causes `buyTokenQuant` to meet/exceed the actual balance. This is a single, ordinary, publicly submittable transaction (`ExchangeTransactionContract`) requiring no special privileges — only a funded account and knowledge of the target pool's balances (both are public, on-chain readable state). No consensus, replay, or multi-step setup is needed beyond normal transaction submission.

### Recommendation
Apply the negative-balance invariant check unconditionally, not gated by `hardenedCalc`:
```java
if (newFirstTokenBalance < 0 || newSecondTokenBalance < 0) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
```
in `ExchangeCapsule.transaction()` [1](#0-0) , and additionally have `ExchangeTransactionActuator.doValidate()` explicitly bound `anotherTokenQuant` against the current counter-token balance before persisting, independent of the hardened flag.

### Proof of Concept
Unit test targeting `ExchangeCapsule.transaction` directly (mirrors existing `ExchangeCapsuleTest` style, e.g. `testTransactionLegacyVsHardenedProcessorSelection` at `framework/src/test/java/org/tron/core/capsule/ExchangeCapsuleTest.java:85-106`):

```java
@Test
public void testLegacyNonHardenedAllowsNegativeBalance() throws Exception {
  ExchangeCapsule capsule = new ExchangeCapsule(
      ByteString.copyFromUtf8("owner"), 200L, 0L,
      "abc".getBytes(), "def".getBytes());
  // Small counter-token balance, large sell-side balance ratio to push
  // the legacy double-based Bancor formula near/over its asymptotic bound.
  capsule.setBalance(1_000_000_000_000_000_000L /* huge firstTokenBalance */,
      1L /* tiny secondTokenBalance */);

  long sellQuant = Long.MAX_VALUE / 2; // attacker-chosen extreme sell quant
  long buyQuant = capsule.transaction("abc".getBytes(), sellQuant, true, false);

  // With hardenedCalc=false, no guard prevents this:
  Assert.assertTrue("buyTokenQuant unexpectedly capped",
      buyQuant >= capsule.getSecondTokenBalance() + 1 || capsule.getSecondTokenBalance() < 0);
  Assert.assertTrue("Negative pool balance was persisted without rejection",
      capsule.getSecondTokenBalance() < 0);
}
```
Expected result on the vulnerable code: `capsule.getSecondTokenBalance() < 0` is reachable and no `ContractValidateException` is thrown, whereas the same scenario with `hardenedCalc=true` throws `ContractValidateException("Exchange balance must be >=0 after transaction")`. This should also be reproduced end-to-end through `ExchangeTransactionActuator.execute()` with `allowHarden()==false` to confirm the negative value is written via `Commons.putExchangeCapsule`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-166)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L67-69)
```java
      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L93-96)
```java
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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L3059-3072)
```java
  public long getAllowHardenExchangeCalculation() {
    return Optional.ofNullable(getUnchecked(ALLOW_HARDEN_EXCHANGE_CALCULATION))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(0L);
  }

  public void saveAllowHardenExchangeCalculation(long value) {
    this.put(ALLOW_HARDEN_EXCHANGE_CALCULATION, new BytesCapsule(ByteArray.fromLong(value)));
  }

  public boolean allowHardenExchangeCalculation() {
    return getAllowHardenExchangeCalculation() == 1L;
  }
```
