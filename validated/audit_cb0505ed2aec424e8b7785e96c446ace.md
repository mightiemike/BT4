### Title
Legacy (non-hardened) `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` path lacks reserve-invariant checks, allowing exchange reserve corruption - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
`ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` compute the bancor-style relay/supply conversion using raw `double` math and cast the result directly to `long` with no bounds or sanity checking. Unlike the newer hardened path (`SafeExchangeProcessor`, selected only when `allowHardenExchangeCalculation` is enabled), the default/legacy code path in `ExchangeCapsule.transaction` only checks for negative resulting balances when `hardenedCalc == true` [1](#0-0) , meaning the legacy path can silently persist a corrupted/negative-then-wrapped reserve balance.

### Finding Description
`ExchangeProcessor.exchangeToSupply` computes `issuedSupply` via `Maths.pow` on doubles and truncates to `long` with no clamping [2](#0-1) , and `exchangeFromSupply` raises `(1 + supplyQuant/supply)` to the power `2000.0`, which grows extremely fast as `supplyQuant/supply` increases [3](#0-2) . When an exchange pool has a very small reserve on one side (e.g., a freshly created or heavily drained pool) and the attacker sells a quantity close to the `ExchangeBalanceLimit` cap (default `1_000_000_000_000_000L`) [4](#0-3) , the computed `buyTokenQuant` can balloon to values far larger than the counter-token's actual balance in the pool.

This result flows into `ExchangeCapsule.transaction`, which, in the legacy (non-hardened) branch, computes `newSecondTokenBalance = secondTokenBalance - buyTokenQuant` using plain `long` subtraction (no `subtractExact`), and only validates `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` when `hardenedCalc == true` [5](#0-4) . In the default (non-hardened) legacy path this check is skipped entirely, so an inflated `buyTokenQuant` is written straight into the exchange reserve and credited to the attacker's account via `accountCapsule.addAssetAmountV2`/`setBalance` in `ExchangeTransactionActuator.execute` [6](#0-5) .

`ExchangeTransactionActuator.doValidate` only enforces `tokenQuant > 0`, `tokenExpected > 0`, non-zero initial balances, `ExchangeBalanceLimit` on the *sold* token side, and that `anotherTokenQuant >= tokenExpected` [7](#0-6) . Critically, it never checks that `anotherTokenQuant` (the computed payout) is bounded by the exchange's actual reserve of that token — the only such bound exists in the hardened path, which is opt-in via the `AllowHardenExchangeCalculation` committee proposal and not the default state.

### Impact Explanation
If exploitable in practice, this allows an unprivileged account to receive tokens/TRX far in excess of what the exchange pool actually holds, corrupting the pool's on-chain reserve accounting and potentially minting value that isn't backed by real deposits — matching the "Asset/accounting corruption (Critical)" impact class.

### Likelihood Explanation
Exploitability depends on precise numeric conditions: the attacker needs a pool with a very small counter-token reserve relative to the allowed sell quantity so that the bancor exponentiation (`^2000`) overflows the intended bounded range. This requires:
- Finding/creating an exchange pair with an extremely lopsided reserve ratio (poor liquidity pools are permitted since `ExchangeCreateActuator`/`ExchangeInjectActuator` only enforce a maximum balance, not a minimum ratio).
- The default (non-hardened) calculation path being active, i.e., `AllowHardenExchangeCalculation` not enabled — this is the default state absent a passed committee proposal.
- No committee/privileged role needed to trigger the exploit itself; only ordinary funded accounts broadcasting `ExchangeCreateContract`/`ExchangeTransactionContract`.

I was not able to fully verify within available tool calls (1) whether `Maths.pow`/`disableJavaLangMath`'s default configuration causes `addAssetAmountV2`'s `addExact` to throw on overflow (which would abort the exploit transaction before persisting the exchange corruption) rather than silently wrap, and (2) the exact numeric threshold at which `(long) issuedSupply`/`(long) exchangeBalance` casts produce a corrupting rather than merely large-but-safe value. These are necessary to confirm end-to-end exploitability versus the transaction simply failing with `ArithmeticException`/`ContractExeException` (which would make the finding non-exploitable, only a DoS on that single transaction).

### Recommendation
- Apply the same reserve-invariant checks (`newFirstTokenBalance >= 0 && newSecondTokenBalance >= 0`, and ideally `buyTokenQuant <= existing counter-token balance`) unconditionally in `ExchangeCapsule.transaction`, not just when `hardenedCalc == true`.
- Make the `SafeExchangeProcessor` (BigDecimal-based, exact) path the default rather than gating it behind an opt-in committee proposal, or add explicit bounds checking directly inside `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` to reject `NaN`/`Infinity`/out-of-range results before returning.
- Add an explicit actuator-level validation that the computed `anotherTokenQuant` cannot exceed the exchange's current balance of `anotherTokenID`.

### Proof of Concept
Recommend a JUnit test (in the style of `ExchangeTransactionActuatorTest`/`ExchangeCapsuleTest`) that:
1. Creates an exchange pool with an extremely small counter-token balance (e.g., `firstTokenBalance = 1`, `secondTokenBalance = 1`) via `ExchangeCreateActuator` (or directly via `ExchangeCapsule.setBalance`).
2. Broadcasts an `ExchangeTransactionContract` selling `firstToken` with `quant` close to `dynamicStore.getExchangeBalanceLimit()` (`1_000_000_000_000_000L`), non-hardened mode (default `AllowHardenExchangeCalculation = 0`).
3. Asserts whether `ret.getExchangeReceivedAmount()` (or the resulting `exchangeCapsule.getSecondTokenBalance()`) exceeds the original pool holdings or goes negative/wraps — confirming reserve-invariant violation — versus the transaction throwing `ArithmeticException`/`ContractExeException` cleanly.

Given the noted verification gaps around overflow handling defaults, this PoC should be executed against the actual codebase to confirm the exploit succeeds end-to-end rather than being blocked by `addExact`/strict-math guards.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-168)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-29)
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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L556-560)
```java
    try {
      this.getExchangeBalanceLimit();
    } catch (IllegalArgumentException e) {
      this.saveExchangeBalanceLimit(1_000_000_000_000_000L);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-93)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L182-221)
```java
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

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
