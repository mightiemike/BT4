### Title
Non-hardened `ExchangeCapsule.transaction` skips negative-balance guard, allowing pool corruption via adversarial low-liquidity swaps - ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
`ExchangeCapsule.transaction(byte[], long, boolean, boolean)` only validates that resulting balances are non-negative when `hardenedCalc` is `true`; when `hardenedCalc` is `false` (the default, since `allowHardenExchangeCalculation` is a governance-gated dynamic property that starts disabled), the double-based `ExchangeProcessor.exchange` path can return a `buyTokenQuant` that exceeds the actual `buyTokenBalance` for a low-liquidity pool, and the resulting negative balance is written to state with no check at all.

### Finding Description
`ExchangeTransactionActuator.execute`/`doValidate` call `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` [1](#0-0) , where `allowHarden()` simply reflects `dynamicPropertiesStore.allowHardenExchangeCalculation()` [2](#0-1) . Both `allowStrictMath` and `allowHardenExchangeCalculation` are chain-parameter proposals that must be turned on by committee governance — until such a proposal passes, both remain in their off state, so ordinary unprivileged users are calling `transaction` with `hardenedCalc=false` on every network that has not yet enabled hardening.

In `ExchangeCapsule.transaction`, when `hardenedCalc` is `false`:
- `Processor processor = new ExchangeProcessor(supply, useStrictMath)` is used instead of `SafeExchangeProcessor` [3](#0-2) .
- `buyTokenQuant` is computed via plain double-based math in `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply`, which uses `Math.pow`-derived ratios that are unbounded for extreme `sellTokenQuant`/`balance` ratios [4](#0-3) .
- The new balances are computed with plain `+`/`-` arithmetic instead of `StrictMathWrapper.addExact`/`subtractExact` [5](#0-4) .
- Critically, the only negative-balance check is gated behind `hardenedCalc`: `if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0))` [6](#0-5) . When `hardenedCalc` is `false`, this branch is skipped entirely, and the possibly-negative `newFirstTokenBalance`/`newSecondTokenBalance` is written directly into the persisted `Exchange` proto [7](#0-6) .

`ExchangeTransactionActuator.doValidate` only bounds the *sell*-side balance against `dynamicStore.getExchangeBalanceLimit()` [8](#0-7)  and checks `anotherTokenQuant < tokenExpected` [9](#0-8) ; it never verifies that `anotherTokenQuant` (the computed `buyTokenQuant`) is `<=` the actual `buyTokenBalance` in the pool. Because `getExchangeBalanceLimit` bounds only the absolute post-trade sell-side balance and not the ratio between `sellTokenQuant` and the pool's existing balances, an attacker can create (via `ExchangeCreateActuator`) or find a pool with tiny `firstTokenBalance`/`secondTokenBalance` and then submit an `ExchangeTransactionContract` with a `sellTokenQuant` many orders of magnitude larger than the pool's balance (up to the balance limit). This produces an extreme ratio inside `Maths.pow(1.0 + quant/newBalance, 0.0005)` and the reciprocal `pow(...,2000.0)` step in `ExchangeProcessor`, which is unbounded/imprecise in double arithmetic and can yield a `buyTokenQuant` far exceeding the pool's actual opposite-side balance (or even non-finite/garbage values when cast from `double` to `long`). Since `hardenedCalc` is `false`, the resulting negative balance is committed to the `ExchangeCapsule`/`ExchangeV2Store` without any guard.

### Impact Explanation
A corrupted (negative) `firstTokenBalance` or `secondTokenBalance` on an `Exchange` breaks the pool's fundamental accounting invariant. Once one side goes negative, subsequent `ExchangeProcessor.exchange` calls operate on garbage inputs, and legitimate users attempting `ExchangeWithdrawActuator`/`ExchangeInjectActuator`/further `ExchangeTransactionActuator` calls against that pool will get further-corrupted or reverted results, effectively locking or draining any remaining real value contributed by other liquidity participants in that pool.

### Likelihood Explanation
This requires only unprivileged actions: (1) `ExchangeCreateActuator` to create a low-liquidity pair (or finding one already low-liquidity), and (2) one or more `ExchangeTransactionActuator` transactions with a `quant` far exceeding the pool's balance. It is reachable on any network/state where `allowHardenExchangeCalculation` has not yet been enabled by governance — which is the default until a specific chain parameter proposal passes — so no governance/admin action by the attacker is needed, only the network's current parameter defaults. The main uncertainty is the exact numeric threshold at which the double-based `Math.pow` computation in `ExchangeProcessor` overshoots the real buy balance; this needs to be confirmed with a concrete Java unit test (see PoC) since the repo's own hardened-path tests (`ExchangeProcessorTest`) only assert results are within bounds for the `SafeExchangeProcessor`, not for the plain `ExchangeProcessor` under adversarial ratios.

### Recommendation
Remove the `hardenedCalc &&` condition so the negative-balance check in `ExchangeCapsule.transaction` (chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java, lines 160-162) always applies regardless of `hardenedCalc`, and additionally validate in `ExchangeTransactionActuator.doValidate`/`execute` that the computed `buyTokenQuant` never exceeds the current opposite-side pool balance before persisting.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/capsule/ExchangeCapsuleTest.java

@Test
public void testNonHardenedTransactionCanGoNegative() throws Exception {
  byte[] key = ByteArray.fromLong(1);
  ExchangeCapsule capsule = chainBaseManager.getExchangeStore().get(key);
  // Low-liquidity pool
  capsule.setBalance(1L, 1L);

  // Adversarial quant, far larger than the pool's balances,
  // allowed by ExchangeTransactionActuator.doValidate's balance-limit check
  // (which only bounds the sell side, not the ratio to buy side).
  long adversarialQuant = 1_000_000_000_000L;

  long buyQuant = capsule.transaction("abc".getBytes(), adversarialQuant, false, false);

  // Expect: with hardenedCalc=false, no exception is thrown even though
  // buyQuant > original secondTokenBalance, and the persisted balance is negative.
  Assert.assertTrue("PoC: buyTokenQuant exceeds pool buy-side balance",
      buyQuant > 1L);
  Assert.assertTrue("PoC: exchange balance corrupted to negative",
      capsule.getSecondTokenBalance() < 0);

  // Contrast: same adversarial input with hardenedCalc=true must throw
  // ContractValidateException instead of corrupting state.
  ExchangeCapsule hardenedCapsule = new ExchangeCapsule(
      ByteString.copyFromUtf8("owner"), 2, 0, "abc".getBytes(), "def".getBytes());
  hardenedCapsule.setBalance(1L, 1L);
  Assert.assertThrows(ContractValidateException.class,
      () -> hardenedCapsule.transaction("abc".getBytes(), adversarialQuant, true, true));
}
```
Expected result: the non-hardened call succeeds and leaves `secondTokenBalance < 0` (or another corrupted value derived from double-cast garbage), while the hardened call throws `ContractValidateException`, confirming the guard is bypassed only in the non-hardened path.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L64-69)
```java
      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L140-158)
```java
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L160-162)
```java
    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L163-166)
```java
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();
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
