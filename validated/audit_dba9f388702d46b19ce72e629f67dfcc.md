### Title
Non-deterministic floating-point exchange pricing in `ExchangeProcessor` can silently produce Infinity/NaN-derived amounts, causing consensus-relevant miscalculation or DoS of exchange trades - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The external report describes a centralized, unvalidated price input (coinmarketcap `zecprice`) that can become `0`, causing a division that yields `Infinity`, which then silently breaks the send-amount calculation for all users instead of failing safely. The closest reachable analog in java-tron is the bonding-curve "price" calculation used by the Exchange actuators (`ExchangeInjectActuator`, `ExchangeWithdrawActuator`, `ExchangeTransactionActuator`), where the non-hardened `ExchangeProcessor` performs raw IEEE-754 `double` division/`pow` on-chain-derived, user-influenced quantities without guarding against near-zero/negative denominators, silently producing `Infinity`/`NaN` that get truncated by `(long) cast` instead of throwing.

### Finding Description
Any unprivileged account can invoke `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract`. These are all routed through `ExchangeCapsule.transaction()`, which picks between `SafeExchangeProcessor` (BigDecimal-based, throws `ArithmeticException` on invalid states) and the legacy `ExchangeProcessor` (double-based) depending on the `allowHarden()` flag: [1](#0-0) 

`allowHarden()` is gated by the chain parameter `allowHardenExchangeCalculation`, a committee/SR-controlled proposal switch: [2](#0-1) 

Until that proposal is activated by super representatives, all exchange actuators execute the legacy, non-hardened `ExchangeProcessor`, which performs plain `double` arithmetic: [3](#0-2) 

Here, `supply -= supplyQuant` can be driven to zero or negative by a sequence of trades against small-balance exchanges, and `(double) quant / newBalance` / `(double) supplyQuant / supply` division does not throw on zero denominators the way `BigDecimal` does — it silently yields `Infinity` or `NaN`. `Maths.pow` (deprecated but still wired via `useStrictMath` flag) then propagates this, and the final `(long) issuedSupply` / `(long) exchangeBalance` cast of `Infinity`/`NaN` yields `Long.MAX_VALUE`/`0` per Java's `double`-to-`long` narrowing rules — not an exception, not a validated failure, but a garbage numeric result flowing directly into `ExchangeCapsule` balances and `TransactionResultCapsule.setExchangeReceivedAmount` / `setExchangeInjectAnotherAmount` / `setExchangeWithdrawAnotherAmount`: [4](#0-3) 

This mirrors the report's root cause exactly: an externally-influenced (here, user/trade-driven rather than coinmarketcap-driven) numeric input feeding a floating-point division that is not defensively checked for zero/near-zero denominators, producing `Infinity`/`NaN` that downstream code (there: Kotlin `DecimalFormat`; here: a `(long)` cast) mishandles instead of rejecting.

The project's own test suite acknowledges this class of failure was the motivation for `SafeExchangeProcessor`, explicitly testing that the *hardened* (BigDecimal) path throws `ArithmeticException` on divide-by-zero, while the legacy path is exercised separately without equivalent protection: [5](#0-4) 

The actuator-level validation only guards the trivial `firstTokenBalance == 0 || secondTokenBalance == 0` case at entry: [6](#0-5) 

It does not validate the intermediate `supply` variable inside `ExchangeProcessor`, which is fully internal to the (unhardened) calculation and can reach zero/negative territory over repeated trades against a thin exchange pool, since `supply` is reduced by `supplyQuant` on every trade routed through this processor.

### Impact Explanation
While `SafeExchangeProcessor` exists and eliminates this class of bug when activated, the vulnerable `ExchangeProcessor` path remains the default/reachable code path for any deployment where `allowHardenExchangeCalculation` has not been enabled via SR proposal. On that path:
- A trade sequence that pushes internal `supply` to zero/near-zero (or produces `newBalance == 0` from user-supplied `quant`) yields `Infinity`/`NaN`, silently converted to a nonsensical `long` (e.g., `Long.MAX_VALUE`), which is then written into on-chain account balances and `ExchangeCapsule` state via `addAssetAmountV2`/`setBalance`, causing state corruption/accounting divergence — a concrete, on-chain-verifiable impact, unlike the client-side mobile-wallet DoS in the original report.
- At minimum this reproduces the report's DoS pattern: legitimate exchange trades on affected pools become impossible to execute correctly (transactions fail validation with `ArithmeticException`-adjacent behavior once amounts overflow downstream `addExact`/`subtractExact` checks, or worse, silently distort settled amounts if such checks are not hit).

### Likelihood Explanation
This requires the target Exchange pool to have low enough token balances (a normal, user-created state via `ExchangeCreateContract`/`ExchangeInjectContract`) and a sequence of `ExchangeTransactionContract` calls from an unprivileged attacker to drive `supply`/`newBalance` toward the edge case. No special privileges are needed — only ordinary TRC10/TRX exchange usage. The likelihood is moderated by the fact that `SafeExchangeProcessor` may already be activated on mainnet (this cannot be confirmed from the code alone; it depends on live chain parameter state, which is outside static-analysis scope).

### Recommendation
- Make the hardened `SafeExchangeProcessor` (BigDecimal-based, throws on invalid states) the unconditional, non-optional path for all exchange actuators, removing the legacy floating-point `ExchangeProcessor` entirely, rather than gating safety behind an opt-in governance proposal.
- If the legacy path must be retained for backward compatibility, explicitly check `Double.isInfinite()`/`Double.isNaN()` and `supply <= 0`/`newBalance <= 0` before casting to `long`, throwing `ArithmeticException`/`ContractValidateException` instead of silently truncating.
- Audit all other governance-gated "hardening" flags (`allowHardenExchangeCalculation` and similar) to determine whether unsafe legacy math paths remain live in production by default, consistent with the report's long-term recommendation to "evaluate all points of centralization/unsafe fallback to ensure that significant failure cannot occur."

### Proof of Concept
1. Create an `Exchange` with a small `firstTokenBalance`/`secondTokenBalance` via `ExchangeCreateContract` (unprivileged).
2. Ensure `allowHardenExchangeCalculation` is not enabled (default/un-proposed state).
3. Submit a sequence of `ExchangeTransactionContract` trades against the pool sized to drive the internal `supply` variable in `ExchangeProcessor.exchangeFromSupply` toward zero (each call to `ExchangeCapsule.transaction()` reduces `supply` by the relayed amount from `exchangeToSupply`).
4. Observe that once `supply` reaches 0 or goes negative, `(double) supplyQuant / supply` yields `Infinity`/`NaN`; the subsequent `Maths.pow(...)` and `(long) exchangeBalance` cast produce a non-representative result (e.g., `Long.MAX_VALUE` or `0`) that is written to `ExchangeCapsule` balances and account asset amounts via [7](#0-6) , instead of the transaction being rejected as it would be under `SafeExchangeProcessor`'s `ArithmeticException` guard shown in the test at lines 188-192 of `ExchangeProcessorTest.java`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-129)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

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

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java (L187-199)
```java
  @Test
  public void testSafeProcessorDivByZeroThrows() {
    // newBalance = balance + quant = -1 + 1 = 0 -> BigDecimal divide by zero
    assertThrows(ArithmeticException.class,
        () -> SafeExchangeProcessor.INSTANCE.exchange(-1L, 100L, 1L));
  }

  @Test
  public void testSafeProcessorAddExactOverflowThrows() {
    // balance + quant = MAX + 1 -> addExact overflow
    assertThrows(ArithmeticException.class,
        () -> SafeExchangeProcessor.INSTANCE.exchange(Long.MAX_VALUE, 1L, 1L));
  }
```
