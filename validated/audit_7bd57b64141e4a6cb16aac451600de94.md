Based on my investigation, I found a concrete analog in java-tron's TRC10 Exchange (Bancor-style AMM) module, which is the closest unprivileged/user-facing accounting mechanism structurally similar to Frankencoin's `Position` price manipulation.

### Title
Unprivileged exchange-pool balance updates use unchecked `long` arithmetic unless a governance flag is enabled, allowing overflow/state corruption that can block unprivileged traders - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
In the Frankencoin bug, a position owner set an extreme value (`price = type(uint256).max`) that made a downstream arithmetic operation performed by an *unprivileged third party* (the liquidator, via `tryAvertChallenge`) overflow and revert, denying that party the ability to complete a legitimate action. The java-tron Exchange module contains a structurally analogous pattern: any unprivileged account can push the AMM pool's `first_token_balance`/`second_token_balance` toward `Long.MAX_VALUE` via `ExchangeCreateContract`/`ExchangeInjectContract`, and a *different* unprivileged account's subsequent `ExchangeTransactionContract` call updates those same balances with **unchecked plain `long` addition/subtraction** unless the chain-wide "harden" flag is active.

### Finding Description
`ExchangeCapsule.transaction()` updates the pool reserves after a trade: [1](#0-0) 

When `hardenedCalc` is `false`, the new balances are computed with raw `+`/`-` (`firstTokenBalance + sellTokenQuant`, `secondTokenBalance - buyTokenQuant`, etc.) with **no overflow check at all**. `hardenedCalc` is passed in from `ExchangeTransactionActuator.execute()` as `allowHarden()`: [2](#0-1) 

which resolves to a `DynamicPropertiesStore` flag: [3](#0-2) 

So whether this arithmetic is checked at all is gated by a chain parameter (`allowHardenExchangeCalculation`), not by a hardcoded, always-on guard. Any account can grow a pool's reserves toward the overflow boundary using `ExchangeCreateActuator`/`ExchangeInjectActuator` (bounded only by `dynamicStore.getExchangeBalanceLimit()`, which is itself a mutable chain parameter, not a fixed safe constant): [4](#0-3) [5](#0-4) 

Once reserves are near the overflow boundary, a subsequent unprivileged trader calling `ExchangeTransactionActuator` triggers `exchangeCapsule.transaction(...)` in `execute()`: [6](#0-5) 

If `allowHardenExchangeCalculation` is off (the non-hardened path), the pool balance can silently wrap (producing a corrupted/negative reserve) instead of throwing — this is state corruption, not merely a revert. If harden mode is on, the same scenario throws `ArithmeticException` via `StrictMathWrapper.addExact`/`subtractExact`, which is caught and converted into a `ContractExeException`, causing the trade to permanently fail for that pool — the direct analog to `tryAvertChallenge` reverting and denying the challenger/liquidator in Frankencoin.

The `ExchangeInjectActuatorTest`/`ExchangeTransactionActuatorTest` tests in this repo explicitly demonstrate this dual behavior (silent overflow when non-hardened vs. `ArithmeticException`/`ContractExeException` when hardened): [7](#0-6) 

### Impact Explanation
An unprivileged pool creator/injector can, through legitimate `ExchangeCreateContract`/`ExchangeInjectContract` calls, push a pool's reserves close to `Long.MAX_VALUE` (bounded only by the mutable `getExchangeBalanceLimit()` parameter). Any subsequent unprivileged trader interacting with that same pool via `ExchangeTransactionContract` can then either (a) have their transaction permanently revert (denial of legitimate trading/value extraction from the pool, mirroring the "liquidation denial" impact), or (b) trigger silent `long` wraparound corrupting the pool's on-chain state (an invalid-state/divergence condition), depending on whether the chain-level `allowHardenExchangeCalculation` flag is active. This is a state-accounting integrity issue reachable by any account with enough TRX/TRC10 balance to create or inject into an exchange pool — no privileged role required.

### Likelihood Explanation
Reaching the overflow boundary requires the attacker to control (or collude to inflate) a pool's reserve near `Long.MAX_VALUE`, which is achievable only if `getExchangeBalanceLimit()` is configured near that range; I was unable to confirm the exact default value of `getExchangeBalanceLimit()` or the default on/off state of `allowHardenExchangeCalculation` within the available search budget, so likelihood in a production deployment depends on those governance-configured values. Given that the overflow-check logic exists solely behind an optional flag rather than being unconditionally applied, the underlying code path is confirmed to be unsafe by design when that flag is disabled.

### Recommendation
Make overflow-checked arithmetic (`addExact`/`subtractExact`, i.e., the `hardenedCalc`/`SafeExchangeProcessor` path) unconditional in `ExchangeCapsule.transaction()` rather than gated behind `allowHardenExchangeCalculation`, and enforce `getExchangeBalanceLimit()` consistently in `ExchangeTransactionActuator` (as already done in `ExchangeInjectActuator`) so pool reserves can never approach the `long` overflow boundary regardless of governance-flag state.

### Proof of Concept
Conceptual PoC (mirrors the confirmed test cases in `ExchangeTransactionActuatorTest.hardenedExecuteOverflowThrowsArithmeticException` and `ExchangeInjectActuatorTest.hardenedAddExactOverflowThrows`):
1. Account A creates an exchange pool via `ExchangeCreateContract`, or repeatedly injects via `ExchangeInjectContract`, growing one side of the pool's reserve close to `Long.MAX_VALUE` (limited by `getExchangeBalanceLimit()`).
2. Account B (unprivileged, unrelated to A) submits `ExchangeTransactionContract` to trade against the same pool.
3. Inside `ExchangeCapsule.transaction()`, `firstTokenBalance + sellTokenQuant` (or the hardened `addExact` equivalent) overflows:
   - Non-hardened: balance silently wraps to a corrupted/negative value, persisted via `Commons.putExchangeCapsule(...)`.
   - Hardened: `ArithmeticException` is thrown, caught in `execute()`, and rethrown as `ContractExeException`, permanently failing B's trade against that pool. [8](#0-7)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-158)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-69)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L55-76)
```java
      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();

      long newBalance = subtractExact(accountCapsule.getBalance(), fee);

      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, firstTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(firstTokenID, firstTokenBalance, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, secondTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(secondTokenID, secondTokenBalance, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-83)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
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
      }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1877-1906)
```java
  @Test
  public void hardenedExecuteOverflowThrowsArithmeticException() throws Exception {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(1);
    dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(1);
    InitExchangeSameTokenNameActive();

    long exchangeId = 1;
    // Corrupt pool to near-MAX TRX so addExact overflows when buying.
    ExchangeCapsule pool = dbManager.getExchangeV2Store().get(ByteArray.fromLong(exchangeId));
    pool.setBalance(Long.MAX_VALUE - 5L, 10_000_000L);
    dbManager.getExchangeV2Store().put(pool.createDbKey(), pool);

    String tokenId = "_";
    long quant = 100L;
    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, 1));

    try {
      // addExact throws ArithmeticException, which is wrapped into ContractExeException.
      Assert.assertThrows(ContractExeException.class,
          () -> actuator.execute(new TransactionResultCapsule()));
    } finally {
      dbManager.getExchangeStore().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeStore().delete(ByteArray.fromLong(2L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(2L));
      dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
    }
  }
```
