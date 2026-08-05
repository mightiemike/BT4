### Title
Non-hardened Exchange balance calculation allows silent integer underflow to negative AMM reserves - (File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java)

### Summary
`ExchangeCapsule.transaction(byte[], long, boolean, boolean)` only validates that the post-trade `newFirstTokenBalance`/`newSecondTokenBalance` are non-negative when `hardenedCalc` is `true`; when `hardenedCalc` is `false` the same balances are computed with plain `+`/`-` and persisted unconditionally, even if negative. Because `ExchangeProcessor.exchange()` uses a bancor-style double/`Math.pow` formula that can return a `buyTokenQuant` larger than the pool's buy-side reserve for extreme `sellTokenQuant` inputs, an ordinary `ExchangeTransactionContract` can drive an Exchange pool's balance negative and have that corrupted state persisted via `Commons.putExchangeCapsule`.

### Finding Description
`ExchangeTransactionActuator.validate()`/`execute()` call `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())`, where `allowHarden()` returns `chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation()` [1](#0-0)  and is passed straight through to `ExchangeCapsule.transaction` [2](#0-1) .

Inside `transaction()`, when `hardenedCalc` is `false`, the non-hardened `ExchangeProcessor` is used and the new balances are computed with plain arithmetic instead of `StrictMathWrapper`, and the negative-balance guard is skipped entirely: [3](#0-2) 

`ExchangeProcessor.exchange()` implements a bancor-formula AMM using `double`/`Math.pow` with an exponent of `2000.0` in `exchangeFromSupply` [4](#0-3) . For a sufficiently large `sellTokenQuant` relative to the reserve, `supplyQuant/supply` grows large enough that `pow(1 + supplyQuant/supply, 2000) - 1` produces a `buyTokenQuant` that exceeds the actual `buyTokenBalance` of the pool, since there is no cap tying the bancor output to the physical reserve. `validate()` does check `firstTokenBalance == 0 || secondTokenBalance == 0` and an upper `balanceLimit`, but it never checks that the computed `buyTokenQuant`/`anotherTokenQuant` is `<= tokenBalance` of the opposite side [5](#0-4) , so nothing else stops the resulting negative balance when `hardenedCalc` is `false`.

The corrupted (negative) balances are then written back unconditionally through `Commons.putExchangeCapsule(exchangeCapsule, ...)` [6](#0-5) , poisoning all subsequent trades on that Exchange pair.

Note: `hardenedCalc` is not directly chosen per-transaction by the attacker — it reflects the chain-wide `allowHardenExchangeCalculation` dynamic property/hard-fork flag. However, on any network where that hardening proposal has not yet been activated (its pre-hardfork/default state), this code path is exercised for every ordinary `ExchangeTransactionContract`, so an unprivileged attacker only needs to pick an extreme `sellTokenQuant` relative to the pool reserves — no privileged action is required to trigger the underflow.

### Impact Explanation
A successful trade in the non-hardened state can leave `Exchange.getSecondTokenBalance()` (or `getFirstTokenBalance()`) negative, persisted to `ExchangeStore`/`ExchangeV2Store`. Negative reserves break the AMM invariant, corrupt all subsequent `exchange()` pricing calculations on that pair (feeding negative `sellTokenBalance`/`buyTokenBalance` into the bancor formula), and can be leveraged to drain the pool or produce further arbitrary/incorrect payouts to the attacker and other traders, i.e. invalid persisted state and potential value duplication/theft on that exchange pair.

### Likelihood Explanation
Feasible only while the network parameter `allowHardenExchangeCalculation` is disabled (its pre-activation default). In that state, the trigger is a single unprivileged, ordinary `ExchangeTransactionContract` with a carefully chosen `sellTokenQuant` (no elevated permission, no other guard blocks it, since `validate()` never bounds `buyTokenQuant` against the opposite-side reserve). This is repeatable against any Exchange pool with small-enough reserves relative to the crafted sell quantity.

### Recommendation
Remove the `hardenedCalc` gate on the balance-sign check so the `< 0` validation (and overflow-safe arithmetic) always applies regardless of the hard-fork flag, and additionally clamp/validate `buyTokenQuant` against the opposing reserve in `ExchangeTransactionActuator.validate()` before allowing the trade to execute.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/capsule/ExchangeCapsuleTest.java (extension)
@Test
public void testNonHardenedUnderflowProducesNegativeBalance() throws Exception {
  ExchangeCapsule exchangeCapsule = new ExchangeCapsule(
      ByteString.copyFromUtf8("owner"), 1, System.currentTimeMillis(),
      "abc".getBytes(), "def".getBytes());
  // small reserves relative to the sell quant, chosen so the bancor
  // formula in ExchangeProcessor.exchange() returns buyTokenQuant > secondTokenBalance
  exchangeCapsule.setBalance(1_000L, 1_000L);

  long buyTokenQuant = exchangeCapsule.transaction(
      "abc".getBytes(), /* extreme */ 500_000_000L,
      /* useStrictMath */ false, /* hardenedCalc */ false);

  // No ContractValidateException thrown, and the invariant is violated:
  Assert.assertTrue(buyTokenQuant > 1_000L);
  Assert.assertTrue(exchangeCapsule.getSecondTokenBalance() < 0);
}
```
Expected result: the call completes without throwing `ContractValidateException`, and `getSecondTokenBalance()` is negative and gets persisted through `Commons.putExchangeCapsule` in `ExchangeTransactionActuator.execute()`, confirming the corrupted state is committed for future trades on the pair.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L95-96)
```java
      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-221)
```java
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

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L140-162)
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

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L31-39)
```java
  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }
```
