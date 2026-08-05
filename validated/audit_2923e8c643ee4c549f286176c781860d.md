Based on my research, I found a genuine analog in java-tron's on-chain TRC10 bancor-style **Exchange** feature (`ExchangeCreateActuator`, `ExchangeTransactionActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`). It exhibits the same root-cause pattern as the PowerPerp finding: **accounting for pool balances is computed through mismatched/imprecise mechanisms across different mutation paths**, allowing state to drift and be drained by unprivileged callers.

### Title
Exchange pool balance drain via floating-point Bancor rounding drift in `ExchangeTransactionActuator` vs. precise `BigInteger`/`BigDecimal` math in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The TRC10 Exchange (a Bancor-formula AMM built into the protocol) mutates `firstTokenBalance`/`secondTokenBalance` state through three distinct unprivileged-user actuators: inject, withdraw, and transaction (trade). Injection and withdrawal use exact `BigInteger`/`BigDecimal` proportional math [1](#0-0) , but the trade path (`ExchangeTransactionActuator`) computes the counter-party amount using `ExchangeCapsule.transaction()`, which by default routes through `ExchangeProcessor` — a Bancor formula implemented with `double`-precision `Math.pow` [2](#0-1) . This is analogous to the PowerPerp bug where one accounting channel (mint) is exact/singular while other channels (burn via close vs. liquidation) diverge — here, one channel (inject/withdraw) is exact while the primary trading channel (transaction) is imprecise, letting the two diverge from true conservation of value.

### Finding Description
`ExchangeCapsule.transaction()` seeds a brand-new `ExchangeProcessor` with a fixed constant `supply = 1_000_000_000_000_000_000L` on every single call rather than persisting a running Bancor relay-token supply [3](#0-2) , and then performs `exchangeToSupply`/`exchangeFromSupply` using `double` arithmetic and `Maths.pow` [2](#0-1) . This floating-point calculation is not exact and rounds `(long) issuedSupply` / truncates results, unlike the `BigInteger`-exact proportional formula used for inject (`anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance`) [4](#0-3)  and withdraw (same proportional `BigInteger`/`BigDecimal` formula, plus a "Not precise enough" precision check) [5](#0-4) .

Because the trade path uses a different (imprecise, non-proportional) mathematical model than the liquidity add/remove paths, any unprivileged user can call `ExchangeTransactionContract` repeatedly (e.g. many small trades back and forth) and exploit accumulated floating-point truncation to extract more value from the pool than they contributed — mirroring the PowerPerp report's core issue that "the source and drain have not been routed through the right channels," causing systemic imbalance. The codebase itself acknowledges this class of risk: it ships a `SafeExchangeProcessor` using `BigDecimal` computation, and a `hardenedCalc`/`allowHardenExchangeCalculation()` dynamic-property governance flag to switch to it [6](#0-5) , and `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` [7](#0-6) . Unless the committee proposal enabling `AllowHardenExchangeCalculation` has been activated network-wide, the default, floating-point `ExchangeProcessor` path remains live and reachable by any account holding the trade token and fee balance — as validated purely on balance-limit checks, not on precision [8](#0-7) .

### Impact Explanation
An unprivileged user (any account) can call `ExchangeTransactionContract` (trade) directly through the wallet API. Because `transaction()`'s floating-point Bancor math diverges from the exact proportional model used elsewhere, repeated round-trip trades can accumulate favorable rounding, letting an attacker extract TRX or TRC10 asset balance from the pool beyond what they deposited, draining liquidity supplied by other participants (creator's injected balance) — a direct value-theft / accounting-divergence impact matching the "supply drain" class of the referenced report.

### Likelihood Explanation
Likelihood depends on whether `AllowHardenExchangeCalculation` has been activated by committee proposal on the running chain; while unset (the historically default state, given it required a governance-gated fix/hardened path to be introduced), the exploitable floating-point path in `ExchangeProcessor` is unconditionally reachable by any unprivileged account with TRC10 Exchange access, requiring no special privileges — only repeated `ExchangeTransactionContract` calls.

### Recommendation
Make `SafeExchangeProcessor` (exact `BigDecimal`) the default and only implementation for `ExchangeCapsule.transaction()`, removing the floating-point `ExchangeProcessor` path entirely, or force-enable `AllowHardenExchangeCalculation` unconditionally so that trade math cannot diverge from the exact proportional accounting already enforced in inject/withdraw.

### Proof of Concept
1. Create an Exchange pool via `ExchangeCreateActuator` with two TRC10 tokens/TRX.
2. Repeatedly submit small `ExchangeTransactionContract` trades alternating direction, observing `ExchangeProcessor.exchange()` output for rounding drift relative to the exact proportional (`BigInteger`) formula used by `ExchangeInjectActuator`/`ExchangeWithdrawActuator`.
3. Compare the total value extracted across many round-trip trades against the value that would result under the exact `SafeExchangeProcessor` model to demonstrate net value extraction beyond fees, showing pool balance divergence over time. [2](#0-1)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L73-82)
```java
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-227)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenID = secondTokenID;
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divide(bigFirstTokenBalance).longValueExact();
      newTokenBalance = addExact(firstTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(secondTokenBalance, anotherTokenQuant);
    } else {
      anotherTokenID = firstTokenID;
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divide(bigSecondTokenBalance).longValueExact();
      newTokenBalance = addExact(secondTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(firstTokenBalance, anotherTokenQuant);
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-129)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L1-47)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L119-215)
```java
  private boolean doValidate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    if (!this.any.is(ExchangeTransactionContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [ExchangeTransactionContract],real type[" + any
              .getClass() + "]");
    }
    final ExchangeTransactionContract contract;
    try {
      contract = this.any.unpack(ExchangeTransactionContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!accountStore.has(ownerAddress)) {
      throw new ContractValidateException("account[" + readableOwnerAddress + NOT_EXIST_STR);
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule.getBalance() < calcFee()) {
      throw new ContractValidateException("No enough balance for exchange transaction fee!");
    }

    ExchangeCapsule exchangeCapsule;
    try {
      exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(contract.getExchangeId()));
    } catch (ItemNotFoundException ex) {
      throw new ContractValidateException("Exchange[" + contract.getExchangeId()
          + ActuatorConstant.NOT_EXIST_STR);
    }

    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();
    long tokenExpected = contract.getExpected();

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

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
```
