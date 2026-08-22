### Title
Legacy (non-hardened) `ExchangeCapsule.transaction()` path allows exchange pool token balances to go negative, corrupting TRC10/TRX exchange accounting - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`ExchangeCapsule.transaction()` computes a new pool balance by subtracting an attacker-influenced `buyTokenQuant` from the pool's stored `firstTokenBalance`/`secondTokenBalance`. When the chain-wide flag `AllowHardenExchangeCalculation` is not active (its default value is `0`), the result is written back to storage with **no check that the result is non-negative**. Only the "hardened" branch (used when the flag is `1`) validates `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` and throws. This mirrors the Connext `PortalFacet.repayAavePortal()` bug pattern: an unchecked subtraction of an attacker-influenceable, user-triggered quantity from a stored balance, guarded only in a "safe" code path that isn't unconditionally enforced.

### Finding Description
`ExchangeCapsule.transaction()` is: [1](#0-0) 

When `hardenedCalc` is `false` (the legacy/default path, selected whenever `DynamicPropertiesStore.getAllowHardenExchangeCalculation()` returns `0`), the new balances are computed with plain `long` subtraction/addition:
```
newFirstTokenBalance = firstTokenBalance - buyTokenQuant;   // or
newSecondTokenBalance = secondTokenBalance - buyTokenQuant;
```
and the subsequent guard `if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) throw ...` is skipped entirely because `hardenedCalc` is `false`. The negative-balance check exists only for the hardened branch, exactly analogous to the missing `require(routerBalance >= amountIn)` check in the original report that was only added as a fix, not enforced unconditionally.

`buyTokenQuant` is produced by the legacy `ExchangeProcessor.exchange()` bancor-like double-precision formula: [2](#0-1) 

This formula is influenced directly by attacker-supplied `sellTokenQuant` (the `quant` field of `ExchangeTransactionContract`/`ExchangeWithdrawContract`). The only bound enforced pre-execution is `tokenBalance <= EXCHANGE_BALANCE_LIMIT` (default `1_000_000_000_000_000L`), not a bound relative to the *other* token's current balance: [3](#0-2) 

Because `ExchangeProcessor`'s double-based exponential math (`Maths.pow(..., 2000.0, ...)`) is not linear, it is possible to construct `sellTokenID`/`sellTokenQuant` combinations that cause `buyTokenQuant` (computed via 64-bit floating point) to exceed the current `buyTokenBalance`, driving the corresponding pool balance negative. This is reachable directly via a broadcast `ExchangeTransactionContract` (`ExchangeTransactionActuator.execute()`) or `ExchangeWithdrawContract` (`ExchangeWithdrawActuator.execute()`), both of which call `exchangeCapsule.transaction(...)`/inline BigInteger-based math built on the same unguarded `firstTokenBalance`/`secondTokenBalance` fields: [4](#0-3) 

The `SafeExchangeProcessor`/hardened-check machinery was clearly added specifically to close this class of bug, but it is gated behind the `AllowHardenExchangeCalculation` dynamic property, which defaults to `0`/off and is only flipped by a committee proposal: [5](#0-4) 

Until that proposal is activated on a given network, the negative-balance guard is absent, i.e., the exact same "guard exists but isn't unconditionally applied" flaw described in the Connext report.

### Impact Explanation
A negative `firstTokenBalance`/`secondTokenBalance` in the on-chain `Exchange`/`ExchangeV2` capsule corrupts the pool's fundamental invariant (reserves can never be negative in an AMM). Subsequent operations (`ExchangeInjectActuator`, `ExchangeWithdrawActuator`, further `ExchangeTransactionActuator` calls) all read these corrupted balances to compute new swap/inject/withdraw quantities using ratios of `firstTokenBalance`/`secondTokenBalance` (e.g. `anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance`). A negative denominator or numerator flips signs and can let an attacker mint/extract TRX or TRC10 tokens they are not entitled to, i.e., unauthorized asset/accounting corruption — the same class of impact called out in the source report ("increase the caller's balance ... arbitrarily high").

### Likelihood Explanation
Anyone can create an exchange pool (`ExchangeCreateActuator`) and broadcast `ExchangeTransactionContract`/`ExchangeWithdrawContract` transactions against it with attacker-chosen `quant` values, with no privileged role required. The only pre-condition is that `AllowHardenExchangeCalculation` has not been enabled by chain governance — this is the default state, so the vulnerable code path is the one actually executed on chains where the hardening proposal hasn't been activated.

### Recommendation
Make the negative-balance check in `ExchangeCapsule.transaction()` unconditional (not gated by `hardenedCalc`), i.e., always validate `newFirstTokenBalance >= 0 && newSecondTokenBalance >= 0` before committing, regardless of the `AllowHardenExchangeCalculation` flag. Additionally, consider bounding `sellTokenQuant`/`buyTokenQuant` against the counter-asset's current pool balance in `ExchangeTransactionActuator`/`ExchangeWithdrawActuator` validation, independent of `EXCHANGE_BALANCE_LIMIT`.

### Proof of Concept
1. Create an exchange pool via `ExchangeCreateContract` with small `firstTokenBalance`/`secondTokenBalance` (e.g., both near the minimum allowed, well under `EXCHANGE_BALANCE_LIMIT`).
2. Ensure `AllowHardenExchangeCalculation` is `0` (default state, no proposal activation needed).
3. Submit `ExchangeTransactionContract` with a `sellTokenID`/`quant` chosen so that `ExchangeProcessor.exchange()`'s bancor formula (`exchangeToSupply` → `exchangeFromSupply`, using `Maths.pow(x, 2000.0)`) returns a `buyTokenQuant` greater than the current opposing pool balance (achievable by picking `quant` close to the pool's sell-side reserve, exploiting the formula's steep growth near large relay ratios).
4. Observe `ExchangeCapsule.transaction()` (legacy branch) commits `newSecondTokenBalance = secondTokenBalance - buyTokenQuant` (or `newFirstTokenBalance`) as negative, with no exception thrown, corrupting the stored `Exchange`/`ExchangeV2` capsule.
5. Follow up with `ExchangeWithdrawContract`/further `ExchangeTransactionContract` calls that use the corrupted (negative) reserve in a ratio computation to extract more tokens/TRX than legitimately deposited into the pool.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-91)
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
