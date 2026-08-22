### Title
Integer overflow in exchange balance accounting bypasses limit check and corrupts exchange reserves when `allowHardenExchangeCalculation` is disabled - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java], [File: chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java])

### Summary
When the `allowHardenExchangeCalculation` dynamic property (hardened calc) is disabled, both the balance-limit guard in `ExchangeTransactionActuator.doValidate` and the arithmetic in `ExchangeProcessor.exchangeToSupply`/`ExchangeCapsule.transaction` perform unchecked `long` addition/subtraction instead of overflow-checked math. An attacker who drives an exchange's token reserve close to `Long.MAX_VALUE` (via repeated `ExchangeInjectActuator` calls, which are gated by the same flag) can then submit an `ExchangeTransactionContract` whose `quant` causes `balance + quant` to wrap to a negative value, silently bypassing the `balanceLimit` check and corrupting the on-chain exchange reserve state.

### Finding Description
`AbstractExchangeActuator.addExact`/`subtractExact` conditionally use `StrictMathWrapper` only `allowHarden()` is true: [1](#0-0) 

When `allowHardenExchangeCalculation` is disabled, the call `tokenBalance = addExact(tokenBalance, tokenQuant);` in `ExchangeTransactionActuator.doValidate` resolves to plain `x + y`, so if `tokenBalance` is near `Long.MAX_VALUE`, the sum silently wraps to a negative number and passes the `tokenBalance > balanceLimit` check instead of failing it: [2](#0-1) 

The subsequent call to `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` propagates the same `hardenedCalc=false` flag into `ExchangeCapsule.transaction`, which then uses unchecked `firstTokenBalance + sellTokenQuant` and only performs the `newFirstTokenBalance < 0` guard when `hardenedCalc` is true: [3](#0-2) 

Internally, `ExchangeProcessor.exchangeToSupply` also performs unchecked addition `long newBalance = balance + quant;`: [4](#0-3) 

Because both the validation-time limit check and the execution-time balance update use the same unchecked-math code path when hardening is off, an attacker who has grown one side of an exchange's reserve close to `Long.MAX_VALUE` can submit a `quant` that wraps `newFirstTokenBalance` (or `newBalance` inside `exchangeToSupply`) to a negative value. This negative reserve is persisted via `Commons.putExchangeCapsule` in `ExchangeTransactionActuator.execute` without any post-check when hardening is disabled: [5](#0-4) 

The `SafeExchangeProcessor` (using `BigDecimal`/`StrictMathWrapper.addExact`) exists specifically to prevent this class of bug, but it is only used when `hardenedCalc` (i.e., `allowHardenExchangeCalculation`) is true: [6](#0-5) 

### Impact Explanation
This is a VALUE_CONSERVATION violation: an exchange's TRX/TRC10 reserve can be forced negative or wrapped, corrupting the AMM-like bonding-curve accounting used by `ExchangeCapsule`. Once a reserve balance is corrupted, subsequent `exchange()` calculations (`exchangeToSupply`/`exchangeFromSupply`) operate on garbage values, which can be leveraged to mint/drain assets disproportionately from the exchange, i.e., asset/accounting corruption as described in the question's scoped impact.

### Likelihood Explanation
The exploit path is fully reachable by an unprivileged, funded account broadcasting standard `ExchangeTransactionContract` (and, for setup, `ExchangeInjectContract`) transactions — no privileged role is required. However, it is entirely contingent on the network-wide dynamic property `allowHardenExchangeCalculation` being disabled (the `hardenedCalc=false` state referenced throughout the question). If that flag is enabled (as is intended by the code's own hardening mechanism, presumably activated on live networks via committee proposal), `StrictMathWrapper.addExact`/`subtractExact` throw `ArithmeticException` on overflow in both `doValidate` and `execute`, and `ExchangeCapsule.transaction` additionally rejects negative resulting balances — fully blocking the attack. The attacker would also need to accumulate a reserve near `Long.MAX_VALUE` through many `ExchangeInjectActuator` calls (also only exploitable while hardening is off), which is costly in bandwidth/fees but not otherwise restricted.

### Recommendation
Make `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` always use overflow-checked arithmetic (e.g., always delegate to `SafeExchangeProcessor`/`Math.addExact`) regardless of the `allowHardenExchangeCalculation` flag, or ensure `AbstractExchangeActuator.addExact`/`subtractExact` always use `StrictMathWrapper` for the balance-limit check in `doValidate`, decoupling the safety check from the legacy/compat calculation path. At minimum, retire the unchecked-math branch entirely once `allowHardenExchangeCalculation` has been activated network-wide, since it is the source of this exploitable gap.

### Proof of Concept
```java
// Illustrative JUnit-style PoC using ExchangeCapsule directly (chainbase module)
ExchangeCapsule exchangeCapsule = new ExchangeCapsule(
    ByteString.copyFrom(ownerAddress), 1L, 0L, "_".getBytes(), "TOKEN".getBytes());
exchangeCapsule.setBalance(Long.MAX_VALUE - 10, 1_000_000_000L);

// hardenedCalc = false (allowHardenExchangeCalculation disabled)
long result = exchangeCapsule.transaction(
    "_".getBytes(), /* sellTokenQuant */ 100L, /* useStrictMath */ false, /* hardenedCalc */ false);

// Expected (buggy) behavior: no ContractValidateException thrown, and
// exchangeCapsule.getFirstTokenBalance() is negative (wrapped), violating VALUE_CONSERVATION.
assertTrue(exchangeCapsule.getFirstTokenBalance() < 0);
```
At the actuator level, this corresponds to an unprivileged account first calling `ExchangeInjectActuator` repeatedly to grow `firstTokenBalance` near `Long.MAX_VALUE` (while `allowHardenExchangeCalculation` is disabled on the network), then broadcasting an `ExchangeTransactionContract` with a `quant` large enough that `firstTokenBalance + quant` overflows, expecting `ExchangeTransactionActuator.validate()` to reject it via the `balanceLimit` check but instead observing it succeed with a corrupted (negative/wrapped) `firstTokenBalance` stored via `Commons.putExchangeCapsule`.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L93-97)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-205)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-162)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-20)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);
```
