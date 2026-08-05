### Title
Exchange pool becomes permanently and irrecoverably closed once a token balance reaches zero, with no governance or actuator path to reopen it - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`, `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
The `Exchange` (Bancor-style TRX/TRC10 liquidity pool) mechanism in java-tron hard-codes a check that once either token reserve in a pool reaches `0`, all future trade/inject/withdraw operations on that pool permanently revert with `"the exchange has been closed"`. There is no actuator, proposal type, or admin function anywhere in the codebase that can re-fund or reset a closed pool's balances, so the pool becomes irrecoverably stuck — directly analogous to the reported TwapOracle issue where a permanently "stuck" price source causes `consult()` to always revert with no governance recovery path.

### Finding Description
`ExchangeTransactionActuator.doValidate()` guards every trade with: [1](#0-0) 
and the identical check exists in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` (confirmed by grep hits for the string `"the exchange has been closed"` in all three actuators). Once `firstTokenBalance == 0 || secondTokenBalance == 0` for a given `ExchangeCapsule`, this check fires on every subsequent call for that exchange ID, for every user, forever.

The balance can reach zero either through the creator's `ExchangeWithdrawActuator` (a trusted-role action, excluded per scope rules) or, more importantly, through the core AMM math in `ExchangeCapsule.transaction()`: [2](#0-1) 
which is invoked by any unprivileged user calling `ExchangeTransactionActuator`. In non-hardened mode (`useStrictMath`/legacy `ExchangeProcessor`, using `double` arithmetic and `Maths.pow`), a sufficiently large `sellTokenQuant` relative to a low-liquidity pool's reserves can drive `buyTokenQuant` to consume the entirety of one side's reserve, landing the resulting balance exactly at (or effectively at) `0`. When `hardenedCalc` is enabled, `SafeExchangeProcessor.exchange()` is used instead: [3](#0-2) 
and `ExchangeCapsule.transaction()` explicitly checks for negative balances and throws `ContractValidateException` if `newFirstTokenBalance < 0 || newSecondTokenBalance < 0`: [4](#0-3) 
Additionally, as demonstrated in the repository's own regression test, a corrupted/extreme reserve state combined with hardened math causes `StrictMathWrapper.addExact`/`subtractExact` to throw `ArithmeticException` on every future call in that direction: [5](#0-4) 

Once a pool enters either the "balance == 0 closed" state or the "hardened overflow" state, there is no recovery path:
- No actuator exists to re-inject liquidity into a closed pool from a party other than the exchange itself trading (which is blocked by the same check).
- The only global lever, `ALLOW_HARDEN_EXCHANGE_CALCULATION`, is a chain-wide DynamicProperty toggled via `ProposalUtil`: [6](#0-5) 
Turning it off to unstick one broken pool would re-expose every other pool on the network to the original floating-point precision/overflow bug that hardening was introduced to fix — there is no per-exchange remediation mechanism.

### Impact Explanation
Any TRX/TRC10 exchange pool created through `ExchangeCreateActuator` can become permanently frozen: no user can trade against it, inject into it, or withdraw further, and its liquidity remains locked with no code path to restore functionality. This matches the report's "invalid-state/halt" impact category — a core public-facing feature (on-chain token exchange) becomes irrecoverably broken once triggered, exactly mirroring TwapOracle's `consult()` becoming permanently revert-only once the Chainlink aggregator sticks.

### Likelihood Explanation
Reaching exactly zero balance or the hardened-overflow condition through normal large trades against a low-liquidity pool is an edge case, similar in spirit to the "very unlikely" Chainlink-stuck scenario in the original report, which the judge still rated Medium severity because "third-party dependencies/edge-state risks should be treated with respect." Here the risk is entirely first-party (java-tron's own AMM state machine), so it is arguably more directly reachable — an unprivileged trader with sufficient capital, or repeated rounding-favorable trades against a thin pool, can drive a reserve to zero without needing any privileged role.

### Recommendation
Add a governance- or protocol-level recovery mechanism for exchanges that reach a zero-balance or arithmetic-overflow-guarded state — e.g., an admin/committee-gated "reopen exchange" actuator that allows re-seeding balances for a specific `exchangeId`, or relax the permanent "closed" check to allow re-injection even at zero balance (since `ExchangeInjectActuator` should logically be able to restart a pool from `(0, 0)`), rather than leaving it as a dead end enforced identically across all three actuators.

### Proof of Concept
1. Create a low-liquidity exchange pool via `ExchangeCreateActuator` with small `firstTokenBalance`/`secondTokenBalance`.
2. As any unprivileged account, call `ExchangeTransactionActuator` with a `tokenQuant` sized to consume the entire opposing reserve, driving one side of `ExchangeCapsule`'s balance to `0` (non-hardened math) — reference the balance-mutation logic at `ExchangeCapsule.transaction()`: [7](#0-6) 
3. Any subsequent call to `ExchangeTransactionActuator`, `ExchangeInjectActuator`, or `ExchangeWithdrawActuator` against that `exchangeId` now hits the `firstTokenBalance == 0 || secondTokenBalance == 0` guard and reverts with `"the exchange has been closed"` — permanently, since no actuator or proposal exists to reset the capsule's balances.
4. Confirm no `ExchangeReopen`/`ExchangeReset`-style actuator exists in `actuator/src/main/java/org/tron/core/actuator/` (only `ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeTransactionActuator`, `ExchangeWithdrawActuator` are present, all subject to the same closed-state guard).

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

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

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L40-44)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    BigDecimal relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1872-1906)
```java
  /**
   * Hardened mode: corrupt pool with near-MAX balance triggers ArithmeticException
   * from addExact. Demonstrates the overflow-detection guard fires and is not
   * silently swallowed.
   */
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
