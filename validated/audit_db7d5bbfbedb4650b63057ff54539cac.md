## Analysis: Missing overflow/underflow protection in TRX/TRC10 Exchange pool (Bancor-style AMM) — analog to the Space.sol SafeMath report

### Title
Exchange pool balance underflow/overflow is only checked when `ALLOW_HARDEN_EXCHANGE_CALCULATION` is enabled, leaving the default (legacy) path unprotected - (File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java)

### Summary
The external report flags that `Space.sol` performs unchecked arithmetic that can overflow/underflow, and recommends `SafeMath`-style protection everywhere, noting that the fix's PR explicitly documents each place where `SafeMath` is deliberately skipped. The java-tron analog is the Bancor-style TRX/TRC10 `Exchange` (Market/Exchange contracts): its balance-update arithmetic is guarded by overflow/underflow checks **only when the chain governance flag `ALLOW_HARDEN_EXCHANGE_CALCULATION` is enabled**. That flag defaults to `0` (disabled), so on any chain where the committee proposal has not been passed, the legacy unchecked-arithmetic path is what actually executes for every `ExchangeTransactionContract` / `ExchangeInjectContract` / `ExchangeWithdrawContract`.

### Finding Description
`ExchangeCapsule.transaction()` computes new pool balances using either raw arithmetic or `StrictMathWrapper` guarded arithmetic, selected by the `hardenedCalc` flag: [1](#0-0) 

Critically, the negative-balance sanity check that would catch an underflow/overflow is itself gated on `hardenedCalc`:
```
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
``` [2](#0-1) 

`hardenedCalc` is supplied by `AbstractExchangeActuator.allowHarden()`, which reads the dynamic property `ALLOW_HARDEN_EXCHANGE_CALCULATION`: [3](#0-2) 

That property defaults to `0` (disabled) as confirmed by the store accessor (`.orElse(0L)`) and by the proposal test explicitly asserting "current value is 0 (default)": [4](#0-3) [5](#0-4) 

So, by default, `newFirstTokenBalance`/`newSecondTokenBalance` are computed as plain `long + long` / `long - long` with no overflow check and no negative-balance guard: [6](#0-5) 

The traded amount (`buyTokenQuant`) itself, in the default (non-hardened) path, is produced by `ExchangeProcessor`, which relies on `double`-precision floating point (`Math.pow`/`StrictMath.pow` selected by a separate, also-defaulted-off `allowStrictMath` flag) to implement the Bancor "relay token" formula: [7](#0-6) 

Because this is floating-point arithmetic feeding into subsequent unchecked integer subtraction, rounding at the edges of the formula (e.g., very large `sellTokenQuant` relative to the opposite reserve, or repeated small trades approaching the reserve limit) can cause `buyTokenQuant` to be computed slightly larger than the actual `secondTokenBalance`/`firstTokenBalance`, making `newSecondTokenBalance = secondTokenBalance - buyTokenQuant` (or the symmetric case) go negative. In the default configuration this is never rejected, and the corrupted (negative or otherwise incorrect) balance is persisted directly via `Commons.putExchangeCapsule` in `ExchangeTransactionActuator.execute()`: [8](#0-7) 

The exact same unguarded-by-default pattern also affects `ExchangeInjectActuator`, whose non-hardened branch performs raw `+`/`floorDiv(multiplyExact(...))` balance math with no post-condition check: [9](#0-8) 

This mirrors the Space.sol report's root cause precisely: safety math (here, `StrictMathWrapper`/negative-balance checks — java-tron's `SafeMath` analog) exists in the codebase, but is applied conditionally rather than unconditionally, and the unconditional/legacy code path — which is the one actually active by default — remains exposed to overflow/underflow.

### Impact Explanation
Any unprivileged, non-privileged user can call `ExchangeTransactionContract` or `ExchangeInjectContract` (these are ordinary public transaction types, not committee/witness-only). If the Bancor-formula rounding drives `buyTokenQuant` beyond the counter-reserve, the on-chain `Exchange` pool balance (`ExchangeCapsule.firstTokenBalance`/`secondTokenBalance`) can be silently corrupted to a negative or otherwise inconsistent value, since the compensating validation (`newFirstTokenBalance < 0 || newSecondTokenBalance < 0`) is inert unless the committee has separately passed `ALLOW_HARDEN_EXCHANGE_CALCULATION`. A corrupted/negative reserve permanently distorts subsequent exchange rate calculations for that pool (an accounting/state-invariant violation), and can be leveraged to drain disproportionate amounts of the counter-asset from the pool in further trades, since the formula's rate calculation depends on these reserve values.

### Likelihood Explanation
The precondition (`ALLOW_HARDEN_EXCHANGE_CALCULATION == 0`) is the default/unconfigured state of every java-tron network unless the corresponding committee proposal has explicitly been passed. Triggering the underflow requires no special privilege — only a sequence of ordinary `ExchangeTransactionContract`/`ExchangeInjectContract` transactions that push a trade near the edge of a pool's reserve, which is feasible for any account with sufficient TRX/TRC10 balance to interact with an existing `Exchange` pool.

### Recommendation
Make the negative-balance / overflow guard in `ExchangeCapsule.transaction()` unconditional (not gated by `hardenedCalc`), and always use `StrictMathWrapper`/BigInteger-based arithmetic (as `SafeExchangeProcessor` already does) for the reserve balance transitions regardless of the `ALLOW_HARDEN_EXCHANGE_CALCULATION` flag. If backward compatibility of consensus behavior requires the flag to gate rate/formula changes, at minimum the invariant check `newFirstTokenBalance >= 0 && newSecondTokenBalance >= 0` should be enforced unconditionally, matching the explicit-reasoning approach the original report recommends (document explicitly wherever raw arithmetic is intentionally retained, and make sure invariant checks aren't accidentally tied to the same feature flag as the formula itself).

### Proof of Concept
1. Deploy/target an `Exchange` pool (TRX ↔ TRC10) on a network where `ALLOW_HARDEN_EXCHANGE_CALCULATION == 0` (default).
2. As any unprivileged account, submit a series of `ExchangeTransactionContract` trades that repeatedly sell into one side of the pool such that the Bancor-formula-computed `buyTokenQuant` (from `ExchangeProcessor.exchange`) approaches/exceeds the counter-reserve (`secondTokenBalance` or `firstTokenBalance`).
3. Because `hardenedCalc` is `false`, the check at `ExchangeCapsule.java` line 160 (`if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0))`) never fires, so `newSecondTokenBalance = secondTokenBalance - buyTokenQuant` can go negative and is persisted unchanged via `Commons.putExchangeCapsule` in `ExchangeTransactionActuator.execute()`.
4. Subsequent trades against this pool now compute exchange rates using a corrupted reserve value, permanently distorting the pool's accounting.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L136-162)
```java
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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L3059-3072)
```java
  public long getAllowHardenExchangeCalculation() {
    return Optional.ofNullable(getUnchecked(ALLOW_HARDEN_EXCHANGE_CALCULATION))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(0L);
  }

  public void saveAllowHardenExchangeCalculation(long value) {
    this.put(ALLOW_HARDEN_EXCHANGE_CALCULATION, new BytesCapsule(ByteArray.fromLong(value)));
  }

  public boolean allowHardenExchangeCalculation() {
    return getAllowHardenExchangeCalculation() == 1L;
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/utils/ProposalUtilTest.java (L721-724)
```java
    // 3) current value is 0 (default), proposing 0 again -> rejected
    thrown = assertThrows(ContractValidateException.class, proposeZero);
    assertEquals("[ALLOW_HARDEN_EXCHANGE_CALCULATION] has been set to 0, no need to propose again",
        thrown.getMessage());
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L93-99)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
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
