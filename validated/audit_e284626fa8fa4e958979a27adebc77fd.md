## Finding: Exchange pool can be driven to a locked/closed state via ordinary swaps, permanently trapping remaining liquidity

### Title
Unprivileged swap can push one side of a TRON `Exchange` (Bancor-style AMM) balance to zero while the other remains non-zero, permanently freezing the pool and stranding remaining funds - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`, `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
Java-tron's on-chain `Exchange` feature implements a two-asset Bancor-formula pool very similar in spirit to the LatentSwapLEX market: it tracks two token balances and treats the pool as permanently "closed" once either balance hits `0`. `ExchangeTransactionActuator`, `ExchangeInjectActuator`, and `ExchangeWithdrawActuator` all gate on the same check — `firstTokenBalance == 0 || secondTokenBalance == 0` → revert "Token balance in exchange is equal with 0, the exchange has been closed" [1](#0-0) [2](#0-1) . This mirrors the LatentSwapLEX `baseTokenSupply == 0` guard exactly, except here nothing prevents an unprivileged trader from causing exactly this state through normal swap activity, and once triggered, **even the exchange creator's withdraw path is blocked by the same check**, so any residual balance on the non-zero side becomes permanently unrecoverable.

### Finding Description
Any account can call `ExchangeTransactionActuator` to swap into an exchange pool. The actual quantity received is computed by `ExchangeCapsule.transaction()`, which delegates to `ExchangeProcessor.exchange()` (or `SafeExchangeProcessor` when hardened calc is enabled) using a Bancor-style continuous formula [3](#0-2) [4](#0-3) .

Critically, the post-trade balance sanity check that rejects a negative/invalid resulting balance is only performed when `hardenedCalc` is true:
```
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
``` [5](#0-4) 

In the default (non-hardened) path there is no protection at all against the counter-side balance being driven to exactly `0` (or below) by a legitimate large trade — the floating point Bancor curve computation in `ExchangeProcessor` truncates to `long` with no upper bound relative to the actual counter-token balance [6](#0-5) . `doValidate()` in `ExchangeTransactionActuator` only checks that the balances are non-zero **before** the trade, not that they will remain non-zero **after** it [7](#0-6) .

Once one side's balance reaches `0` while the other retains a non-zero balance (the exact analog of "Synth supply non-zero while baseTokenSupply == 0"), the pool is permanently frozen:
- `ExchangeTransactionActuator.doValidate()` reverts for any further swap [1](#0-0) 
- `ExchangeInjectActuator.doValidate()` reverts for any further liquidity injection [8](#0-7) 
- `ExchangeWithdrawActuator.doValidate()` reverts for withdrawal too [2](#0-1) 

The withdraw path is normally restricted to the exchange creator [9](#0-8) , but that privilege becomes irrelevant here: **the creator cannot rescue the remaining, non-zero-side balance either**, because the same zero-balance guard blocks their withdrawal too. The freeze is triggered purely by unprivileged trading via `ExchangeTransactionActuator`.

### Impact Explanation
This is a direct analog of the reported issue's impact class: an operation available to any unprivileged user can push a market/pool's internal accounting into a state flagged as "invalid"/"closed", after which the contract's own defensive check (originally meant to prevent operating on a drained pool) becomes a permanent denial-of-service that also traps the remaining, legitimately-owned balance of the other asset — with no privileged recovery path, since withdraw is gated by the identical check.

### Likelihood Explanation
Reaching exactly zero on one side requires a large trade relative to pool size (more likely in low-liquidity/early-stage or thinly-traded exchanges, similar to the "early stages of the market" condition noted in the original report), so likelihood is Low, matching the original finding's Low likelihood rating; but it requires no special privilege or malicious multi-step setup — a single, otherwise-ordinary large swap by any account can trigger it.

### Recommendation
Add a post-trade check in `ExchangeCapsule.transaction()` (unconditional, not only under `hardenedCalc`) that rejects any resulting `newFirstTokenBalance == 0` or `newSecondTokenBalance == 0` (in addition to `< 0`), similar to how `_calculateMarketState` is recommended to prevent the zero-supply scenario, or enforce a minimum-balance floor akin to minting a small amount to a "dead"/burn-equivalent so a pool can never be fully drained on one side. Additionally, consider allowing the exchange creator to withdraw remaining liquidity even when one balance is zero, so a drained pool does not also strand the counter-asset.

### Proof of Concept
1. Exchange created with `firstTokenBalance = X`, `secondTokenBalance = Y` (Y small relative to what a single trader can supply).
2. Attacker calls `ExchangeTransactionActuator` selling a sufficiently large amount of the first token; `ExchangeCapsule.transaction()` computes `buyTokenQuant` via the uncapped Bancor curve in `ExchangeProcessor.exchange()`, and since `hardenedCalc` is false by default, no check prevents `newSecondTokenBalance` from reaching `0` [10](#0-9) .
3. The trade succeeds, `secondTokenBalance` becomes `0` while `firstTokenBalance` remains large and non-zero.
4. Any subsequent call to `ExchangeTransactionActuator`, `ExchangeInjectActuator`, or `ExchangeWithdrawActuator` on this exchange now reverts with "Token balance in exchange is equal with 0, the exchange has been closed", permanently locking the remaining `firstTokenBalance` inside the exchange with no recovery path.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-212)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L200-203)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```
