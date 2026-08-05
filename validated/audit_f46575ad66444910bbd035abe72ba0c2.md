## Analysis: java-tron on-chain `Exchange` (Bancor-relay AMM) lacks pool-solvency safeguards analogous to the Balancer `WeightedPool` report

### Title
Missing solvency/non-negative-balance check in default (non-hardened) `ExchangeCapsule.transaction()` path allows a swap to output more tokens than a pool holds — ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
The Trail-of-Bits report on Balancer's `WeightedPool` describes a class of bug where pools lack safeguards for edge-case balances, allowing either free tokens or a trapped/insolvent pool. java-tron's built-in `Exchange` feature (`ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, `ExchangeTransactionActuator`) implements a similar two-token AMM (Bancor relay formula) whose balances are on-chain, unprivileged, user-driven state. The same bug class is reachable here: the balance-update path that credits a user with the "bought" token skips a non-negative-balance/solvency check unless an opt-in chain parameter (`allowHardenExchangeCalculation`) is enabled.

### Finding Description
`ExchangeCapsule.transaction()` computes the amount to pay the trader (`buyTokenQuant`) via `Processor.exchange()`, which for the default path is `ExchangeProcessor`, a double-precision (`Math.pow`) Bancor relay calculation: [1](#0-0) 

After computing `buyTokenQuant`, the new pool balances are computed either with strict-math checked arithmetic (`hardenedCalc == true`) or with unchecked plain arithmetic (`hardenedCalc == false`, the default), and the resulting balances are only validated to be non-negative **when `hardenedCalc` is true**: [2](#0-1) 

Whether `hardenedCalc` is used at all is gated by the chain parameter `allowHardenExchangeCalculation`, exposed via `AbstractExchangeActuator.allowHarden()`: [3](#0-2) 

`ExchangeTransactionActuator.doValidate()` only enforces that the counter-token balance is non-zero (to detect an already-closed pool) and that the trader receives at least the trader-supplied `tokenExpected` (slippage protection for the trader) — it never checks that the computed `anotherTokenQuant` is `<=` the pool's actual counter-token balance: [4](#0-3) 

Because the default math path uses floating point (`Math.pow`) rather than exact arithmetic, and the project's own tests demonstrate that the "strict" and "non-strict" double-math paths produce different results for identical inputs (`testStrictMath` asserts `anotherTokenQuant != result`), the output of `exchange()` is not guaranteed to be exactly bounded by the counter-token reserve in every input regime: [5](#0-4) 

This is the direct analog of the Balancer finding: the "safe" invariant/solvency guard (there, `_getDueProtocolFeeAmounts`/balance checks; here, the non-negative-balance assertion) is not applied on the actual live/default execution path, only on an alternate hardened path that is off unless explicitly enabled by governance (`allowHardenExchangeCalculation`).

### Impact Explanation
If a swap on the default (non-hardened) path can compute a `buyTokenQuant` that exceeds the true `secondTokenBalance`/`firstTokenBalance` reserve (via floating-point overshoot at extreme ratios or repeated compounding rounding across many swaps), `ExchangeCapsule.transaction()` will silently write a negative `firstTokenBalance`/`secondTokenBalance` into the capsule (unchecked in the default path), while the actuator's `execute()` unconditionally credits the trader with `anotherTokenQuant` of the real underlying TRX/TRC10 asset via `addAssetAmountV2`/`setBalance`. This is a direct case of a user extracting more value from the pool than it holds — an accounting/solvency violation of unprivileged, on-chain exchange state, matching the report's "free tokens"/insolvent-pool impact category.

### Likelihood Explanation
Exploitability requires finding specific balance/quant combinations where the double-precision Bancor formula overshoots the true reserve — this needs numerical analysis of `Math.pow` behavior in `ExchangeProcessor.exchangeFromSupply` at boundary conditions (e.g., very small `buyTokenBalance`/very large relative `sellTokenQuant`, or long chains of successive swaps accumulating rounding error). The `ExchangeTransactionContract` and `ExchangeInjectContract`/`ExchangeWithdrawContract` are all unprivileged, permissionless actuators callable by any account, and `allowHardenExchangeCalculation` appears to default to disabled (tests explicitly opt in via `saveAllowHardenExchangeCalculation(1)`), so the vulnerable default path is the one actually in production use unless a chain-parameter proposal has enabled hardening. I was not able to fully confirm the exact default value of `allowHardenExchangeCalculation` in `DynamicPropertiesStore.java` (the getter name did not match the exact grep string I used), so this should be verified in a Devin session with full file access.

### Recommendation
- Add an explicit, unconditional post-computation check in `ExchangeCapsule.transaction()` (independent of `hardenedCalc`) that rejects any swap producing `newFirstTokenBalance < 0` or `newSecondTokenBalance < 0`.
- Add an unconditional check in `ExchangeTransactionActuator.doValidate()`/`execute()` that `anotherTokenQuant <= counterTokenBalance` before crediting the trader.
- Consider making `allowHardenExchangeCalculation` mandatory (always on) rather than an opt-in chain parameter, since the "unsafe" default path has demonstrably different (looser) numerical guarantees than the "safe" path, per the project's own `testStrictMath` test.

### Proof of Concept
Not independently reproduced (would require constructing exact reserve/quant values that trigger `Math.pow` overshoot in `ExchangeProcessor.exchangeFromSupply` or a long sequence of compounding swaps under default double-precision math, and confirming `allowHardenExchangeCalculation`'s on-chain default). This is flagged as a concrete code-path/config gap rather than a demonstrated exploit; a background Devin session with full repo/test access is recommended to (a) confirm the default value of `allowHardenExchangeCalculation` in `DynamicPropertiesStore.java`, and (b) fuzz `ExchangeProcessor.exchange()` against `SafeExchangeProcessor.exchange()` to find concrete inputs where the non-hardened result exceeds the counter-token reserve.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
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

**File:** framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java (L272-280)
```java
    for (long[] data : testData) {
      ExchangeProcessor processor = new ExchangeProcessor(supply, false);
      long anotherTokenQuant = processor.exchange(data[0], data[1], data[2]);
      processor = new ExchangeProcessor(supply, true);
      long result = processor.exchange(data[0], data[1], data[2]);
      long safeResult = SafeExchangeProcessor.INSTANCE.exchange(data[0], data[1], data[2]);
      Assert.assertNotEquals(anotherTokenQuant, result);
      Assert.assertEquals(safeResult, result);
    }
```
