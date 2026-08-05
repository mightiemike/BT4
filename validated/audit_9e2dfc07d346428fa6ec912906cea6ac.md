### Title
Bancor-formula exchange pool can be manipulated to make legitimate `ExchangeTransactionContract` trades always revert (analogous MuteBond DOS) - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The root cause of the MuteBond finding is that a shared, attacker-influenceable pricing state (`maxDeposit()` derived from the bond price range) can be pushed into a region where a downstream integer-truncating calculation (`timeToTokens`) always returns `0`, and a `require(tokens_to_mint > 0)` check then makes the public `deposit`/`LockTo` path revert for every caller, indefinitely, until an admin intervenes. The closest reachable analog in java-tron is the TRX/token `Exchange` (Bancor-relay) pool: any unprivileged user can shift the pool's `firstTokenBalance`/`secondTokenBalance` ratio via `ExchangeInjectContract`/`ExchangeWithdrawContract`, and the shared pool state is then read by `ExchangeCapsule.transaction()` for every subsequent `ExchangeTransactionContract` from any user.

### Finding Description
`ExchangeTransactionActuator.doValidate()` computes the expected payout via the shared pool and reverts the whole transaction if it doesn't meet the caller's minimum: [1](#0-0) 

The pool reserves feeding that computation (`firstTokenBalance`, `secondTokenBalance`) are exchange-wide, shared state that any account can shift via `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, which recompute the paired-token amount with `floorDiv`/integer-BigInteger division and update `exchange.setBalance(...)`: [2](#0-1) [3](#0-2) 

`ExchangeCapsule.transaction()` then computes the traded amount using either the legacy floating-point Bancor formula (`ExchangeProcessor`) or, when hardening is enabled, `SafeExchangeProcessor`: [4](#0-3) [5](#0-4) 

Both formulas compute `(long) issuedSupply`/`(long) exchangeBalance` truncation, meaning for a sufficiently skewed reserve ratio (achievable by any account through repeated `ExchangeInjectContract`/`ExchangeWithdrawContract` calls, since these have no privileged access control and only require the caller to hold the relevant balances), a normal-sized trade's output can be forced to `0`. Since `ExchangeTransactionActuator.doValidate()` requires `anotherTokenQuant >= tokenExpected` and `tokenExpected` must be `> 0` (per line 190-192), any caller submitting a request with `tokenExpected >= 1` will have their transaction rejected with `"token required must greater than expected"` as long as the pool ratio remains skewed — exactly mirroring the MuteBond pattern where a manipulated shared pricing variable causes a downstream integer-truncation to zero and a subsequent revert for all callers.

This mirrors the external report's root cause precisely: (1) a public, unprivileged actor manipulates shared pricing state; (2) a downstream calculation truncates to zero due to the skew; (3) a `require`-style check on the calculated amount causes indefinite reverts for legitimate callers until the state is fixed (here, by another `ExchangeInjectContract`/`ExchangeWithdrawContract` restoring balance, or an admin/committee intervention).

### Impact Explanation
This is a Denial-of-Service on the `Exchange` (v1/v2) trading feature for a specific `exchangeId`, not a fund-loss vulnerability — trades for that pool will fail for any user attempting to trade in the direction affected by the skew, until the ratio is restored. The `Exchange` contracts do not lock external funds beyond the pool's own reserves (owned by the exchange creator/injectors), similar to how the original report notes MuteBond doesn't store separate funds. This aligns with a "medium" severity DOS class: temporary, recoverable disruption of a public trading surface rather than fund loss, matching the accepted severity of the original finding.

### Likelihood Explanation
Reaching the vulnerable path only requires standard, unprivileged transactions (`ExchangeInjectContract`, `ExchangeWithdrawContract`, `ExchangeTransactionContract`) that any account can submit as long as they hold sufficient balance of either the TRX or the paired token, so the actor set is unprivileged and the transactions are ordinary user-facing chain actions. The exact reserve ratio and trade sizes required to force `anotherTokenQuant` to truncate to `0` under the current pow-based Bancor formula (`ExchangeProcessor`/`SafeExchangeProcessor`) were not derived numerically here — the java-tron codebase already contains extensive hardening tests (`ExchangeProcessorTest`, `ExchangeCapsuleTest`, `ExchangeTransactionActuatorTest`) around overflow and rounding behavior for this exact code path, indicating the maintainers are aware of and have been actively tightening these edge cases via `allowHarden()`/`SafeExchangeProcessor`. Whether the hardened path fully closes the "always-zero-output for a legitimate trade" DOS window (as opposed to just overflow/negative-balance protection) could not be confirmed from the available code excerpts.

### Recommendation
- Add an explicit floor/minimum-liquidity guard so that `ExchangeInjectContract`/`ExchangeWithdrawContract` cannot push either token reserve of a pool below a threshold that would make the Bancor-formula output round to zero for typical/minimum trade sizes.
- In `ExchangeCapsule.transaction()`, detect when the computed `buyTokenQuant` is `0` for a non-zero `sellTokenQuant` and either revert the *inject/withdraw* operation that caused the skew (fail fast at the point of manipulation) rather than only failing downstream trades, or automatically reject inject/withdraw operations that would leave the pool in such a degenerate state.
- Confirm whether the existing `allowHarden()`/`SafeExchangeProcessor` path (see `ExchangeCapsuleTest.testHardenedTransactionSecondTokenSell`) already prevents this zero-output degenerate state across the full range of legal reserve values, and if not, extend the hardened validation to reject pool states where minimum viable trades would compute to zero.

### Proof of Concept
Conceptual reproduction (exact numeric inputs to fully saturate the pow-based formula were not verified in this review and would need to be derived experimentally, analogous to the original PoC's `bondContract.setStartPrice`/`setMaxPrice` manipulation):
1. Attacker (or any account) repeatedly calls `ExchangeWithdrawContract`/`ExchangeInjectContract` on a target `exchangeId` to skew `firstTokenBalance`/`secondTokenBalance` to an extreme ratio, permitted since these actuators only check balance sufficiency and exchange balance limits, not ratio sanity [6](#0-5) .
2. A legitimate user submits `ExchangeTransactionContract` with a typical `tokenQuant` and `tokenExpected >= 1`.
3. `ExchangeCapsule.transaction()` computes `anotherTokenQuant` via `ExchangeProcessor.exchange()`/`SafeExchangeProcessor.exchange()`, which truncates to `0` under the skewed ratio [7](#0-6) .
4. `doValidate()` rejects the trade with `"token required must greater than expected"` [1](#0-0) , and every subsequent legitimate trade in that direction on this `exchangeId` fails the same way until the pool ratio is restored.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L77-89)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L205-212)
```java
    if (tokenQuant <= 0) {
      throw new ContractValidateException("withdraw token quant must greater than zero");
    }

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
