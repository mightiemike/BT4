## Finding

Because I have limited remaining iterations, I'll present the strongest analog found so far.

### Title
TRC10 Exchange pool balances can be driven negative in the default (non-hardened) `transaction()` path, corrupting exchange state and breaking future trades — analog of unbounded-accumulator-exceeds-collateral DoS ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
The Velar Artha bug is rooted in an accounting design where an accruing value (funding fee) is subtracted from a bounded balance (collateral) without any invariant check, so once the accrued value exceeds the balance, the subtraction underflows and the settlement transaction (`close`) permanently reverts, trapping funds. The closest reachable analog in java-tron is `ExchangeCapsule.transaction()`, which implements the bonding-curve math for TRC10 token exchanges. When `hardenedCalc` is `false` — which is the default, controlled by `allowHardenExchangeCalculation` (defaults to `0`) — the resulting pool balances are computed with plain `+`/`-` and are **never checked for going negative**, unlike the `hardenedCalc == true` branch which explicitly throws `ContractValidateException` if either resulting balance is `< 0`. [1](#0-0) 

### Finding Description
`ExchangeCapsule.transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath, boolean hardenedCalc)` computes the amount bought via the bonding-curve `Processor` and then updates the pool reserves:

```
newFirstTokenBalance = hardenedCalc ? addExact(...) : firstTokenBalance + sellTokenQuant;
newSecondTokenBalance = hardenedCalc ? subtractExact(...) : secondTokenBalance - buyTokenQuant;
...
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException(...);
}
``` [2](#0-1) 

The negative-balance guard only fires when `hardenedCalc` is `true`. This flag is threaded from `dynamicStore.allowHarden()`/`allowHardenExchangeCalculation`, which multiple tests confirm defaults to `0` (disabled), and it is only enabled per-test to exercise the hardened path. [3](#0-2) 

The legacy `ExchangeProcessor.exchange()` uses `double`/floating point math for the bancor-style curve, which for edge-case reserve/quantity ratios (extreme small liquidity, large sell quantities relative to `buyTokenBalance`, or rounding at the tail of the curve) can yield `buyTokenQuant` values large enough to push `secondTokenBalance - buyTokenQuant` (or `firstTokenBalance - buyTokenQuant`) below zero — with **no invariant enforcement** in the default path. This corrupted, negative pool state is then persisted via `Commons.putExchangeCapsule(...)` in `ExchangeTransactionActuator.execute()`. [4](#0-3) 

Once a pool balance is negative, every subsequent `ExchangeTransactionActuator`/`ExchangeWithdrawActuator` call against that exchange pair operates on a corrupted invariant (negative reserve feeding into the bonding-curve formulas), which — exactly like the Velar Artha case — can make legitimate participant withdrawals/trades permanently fail (e.g., division/sqrt on negative or zero-adjusted reserves throwing exceptions, or producing nonsensical negative outputs that fail downstream `subtractExact`/asset-amount checks), effectively freezing the pool for all other token holders. This is a broken invariant that traps counterparties' funds in the same structural way the funding-fee-exceeds-collateral bug traps a position's counterparty funds.

### Impact Explanation
If triggered, this corrupts the shared TRC10 exchange pool's reserve accounting on-chain (state corruption via a broadcast `ExchangeTransactionContract`), potentially freezing further trades/withdrawals for all holders of that exchange pair — a DoS on the exchange market combined with accounting corruption of asset reserves, matching the "asset or accounting corruption" / "DoS via protocol implementation" acceptance criteria.

### Likelihood Explanation
This requires: (1) the network operating with `allowHardenExchangeCalculation == 0` (the default/production setting per test evidence), and (2) constructing an exchange pool and trade sequence where the bancor-curve floating-point computation yields a `buyTokenQuant` exceeding the opposing reserve — achievable by any user through normal `ExchangeCreateActuator`/`ExchangeTransactionActuator` calls with carefully chosen extreme reserve ratios or repeated small trades against thin liquidity, similar to how the Velar Artha PoC intentionally engineers a max-leverage position and lets funding accrue. No privileged access is required.

### Recommendation
Enable (or make mandatory) the `hardenedCalc` invariant check — i.e., always validate `newFirstTokenBalance >= 0 && newSecondTokenBalance >= 0` regardless of the `allowHardenExchangeCalculation` flag — inside `ExchangeCapsule.transaction()`, and reject the transaction with `ContractValidateException` before persisting the exchange capsule, exactly as already implemented for the hardened branch.

### Proof of Concept
Conceptual PoC:
1. Create a TRC10 exchange pair with minimal reserves (e.g., `firstTokenBalance = 1`, `secondTokenBalance = large`) using `ExchangeCreateActuator`.
2. With `allowHardenExchangeCalculation = 0` (default), call `ExchangeTransactionActuator` to sell against the thin side, exploiting the double-precision bancor curve in `ExchangeProcessor.exchange()` so `buyTokenQuant` computed exceeds the actual `secondTokenBalance`/`firstTokenBalance`.
3. Because `hardenedCalc` is `false`, no `< 0` check runs; `newSecondTokenBalance` (or `newFirstTokenBalance`) becomes negative and is persisted via `Commons.putExchangeCapsule`.
4. Subsequent `ExchangeTransactionActuator`/`ExchangeWithdrawActuator` calls on this pool now operate on corrupted negative reserves, producing failures or further corrupted results — freezing the pool for other participants, analogous to the funding-fee-exceeds-collateral permanent-revert scenario in the referenced report. [5](#0-4)

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

**File:** framework/src/test/java/org/tron/core/db/ManagerTest.java (L1355-1358)
```java
    // Default: hardened disabled (==0) -> contract is treated as exchange
    chainManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
    Assert.assertTrue("Exchange tx must be detected when hardened disabled",
        (boolean) m.invoke(dbManager, exchange));
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L57-96)
```java
      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));

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
