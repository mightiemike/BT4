### Title
Exchange creator can skew a pool's token-balance ratio to an extreme via `ExchangeWithdrawActuator`, permanently breaking the Bancor pricing formula and bricking trading for all users - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`java-tron`'s Exchange feature is a Bancor-style AMM where any account can self-appoint as a pool "creator" by calling `ExchangeCreateActuator` (paying only a nominal fee), then withdraw liquidity via `ExchangeWithdrawActuator`. Unlike the RToken `melt()` bug — where an early, unprivileged issuer burns tokens to push `basketsNeeded/totalSupply` to its boundary and permanently break `requireValidBUExchangeRate()` — a java-tron exchange creator can withdraw liquidity down to a near-zero remaining balance on one side of the pool, skewing `firstTokenBalance`/`secondTokenBalance` to an extreme ratio. Because the Bancor pricing math in `ExchangeCapsule.transaction()` / `ExchangeProcessor` is unguarded against such extreme ratios, subsequent trades by any unprivileged trader via `ExchangeTransactionActuator` can be permanently mispriced, reverted, or reduced to zero output, bricking the pool.

### Finding Description
`ExchangeWithdrawActuator.doValidate()` only rejects withdrawal when the *pre-withdrawal* balance is already zero, and only ensures the calculated `anotherTokenQuant` is `> 0`: [1](#0-0) 

There is no lower-bound/minimum-liquidity check on the *resulting* balances after withdrawal — the creator can reduce `firstTokenBalance` (or `secondTokenBalance`) down to `1` (or any tiny value), as long as `anotherTokenQuant` rounds to at least `1`. This mirrors the original bug where `melt()` only checks `totalSupply() == 0` and the post-state BU exchange rate bound, but the attacker can still push the ratio to the extreme edge (`1e27`) without tripping any pre-emptive minimum-liquidity guard.

`execute()` then commits this skewed balance directly into `ExchangeCapsule`: [2](#0-1) 

Every subsequent trade (open to any unprivileged trader) computes pricing from these balances via `ExchangeCapsule.transaction()`, which delegates to `ExchangeProcessor`/`SafeExchangeProcessor`: [3](#0-2) [4](#0-3) 

The Bancor formula relies on `quant/newBalance` ratios raised to fixed exponents (`0.0005`, `2000.0`) using floating-point `Math.pow`. When one side of the pool is reduced to a near-zero balance, the ratio `quant/newBalance` becomes extreme, causing the computed `buyTokenQuant` to become `0`, negative, or wildly disproportionate. `ExchangeTransactionActuator.doValidate()` then either:
- permanently fails legitimate trades via `"token required must greater than expected"` when `anotherTokenQuant` rounds to a value below what any trader could reasonably expect, or
- lets `execute()` proceed with a wrong price extracted from a broken formula, since `execute()` has no independent post-condition re-check equivalent to `requireValidBUExchangeRate()`. [5](#0-4) 

Note also that the upper-bound guard (`getExchangeBalanceLimit`) is enforced on create/inject/transaction paths, but no analogous lower-bound guard exists for withdraw — an asymmetry directly analogous to the RToken report, where `requireValidBUExchangeRate()` bounds the ratio between `1e9` and `1e27` but permits reaching the boundary itself, after which the state becomes unrecoverable.

### Impact Explanation
Once a pool's balance ratio is skewed to an extreme by the (self-appointed, unprivileged) exchange creator, the pool becomes effectively unusable/mispriced for every other trader: `ExchangeTransactionActuator` calls will either revert (`"token required must greater than expected"`) for legitimate trade amounts, or execute with a badly distorted exchange rate that no longer reflects the deposited liquidity. This is an invalid-state/halt condition on public market infrastructure that any unprivileged pool creator can trigger against their own listed pair, denying legitimate unprivileged traders correct or any pricing — directly analogous to the reported `basketsNeeded/totalSupply` DoS in RToken.

### Likelihood Explanation
Creating an exchange and withdrawing liquidity are both unprivileged, fee-only operations available to any account (`ExchangeCreateActuator`, `ExchangeWithdrawActuator`). No governance/witness/committee permission is required, and the withdraw path has no post-withdrawal minimum-balance check, making the skew trivially reachable in a small number of transactions.

### Recommendation
Add a minimum-remaining-balance (or minimum-ratio) check in `ExchangeWithdrawActuator.doValidate()`/`execute()` so that withdrawals cannot reduce either side of the pool below a safe floor relative to the other side, and add a post-trade sanity/rate-bound check in `ExchangeCapsule.transaction()` (mirroring `requireValidBUExchangeRate()`) that rejects trades or withdrawals that would push the balance ratio outside a safe operating range.

### Proof of Concept
1. Attacker calls `ExchangeCreateActuator` to create a pool with `firstTokenBalance = X`, `secondTokenBalance = Y` (self-appointed creator, no special privilege needed beyond the create fee).
2. Attacker (the creator) repeatedly calls `ExchangeWithdrawActuator` specifying `tokenId = firstTokenID` with `quant` close to `firstTokenBalance`, each time satisfying only the checks in `doValidate()` (lines 205–271) — there is no check preventing the *post-withdrawal* balance from becoming `1`.
3. After withdrawal, `firstTokenBalance = 1`, `secondTokenBalance` remains large, producing an extreme ratio.
4. Any subsequent unprivileged trader calling `ExchangeTransactionActuator` against this pool encounters either an always-failing `"token required must greater than expected"` validation or a severely mispriced trade, because `ExchangeCapsule.transaction()`/`ExchangeProcessor.exchange()` compute pricing from the now-extreme `firstTokenBalance`/`secondTokenBalance` pair with no rate-sanity check equivalent to `requireValidBUExchangeRate()`.
5. The pool remains permanently broken for all future traders, as no actuator re-balances or restores the ratio.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-227)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    BigDecimal bigFirstTokenBalance = new BigDecimal(String.valueOf(firstTokenBalance));
    BigDecimal bigSecondTokenBalance = new BigDecimal(String.valueOf(secondTokenBalance));
    BigDecimal bigTokenQuant = new BigDecimal(String.valueOf(tokenQuant));
    final boolean allowHarden = allowHarden();
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
