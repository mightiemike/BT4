### Title
Dust-trade rounding can permanently zero an Exchange pair's token balance, causing a persistent DoS of that TRC10/TRX exchange pool - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
java-tron's built-in bancor-style AMM (`ExchangeCreateContract`/`ExchangeTransactionContract`) is directly analogous to the "first depositor / dust rounding" bug class in the Sherlock report: a bonding-curve pool whose reserve balances can be rounded down to exactly zero by an unprivileged, reachable transaction, after which the pool permanently reverts on any further trade due to a strict `balance == 0` guard, with no way to top the balance back up via the trading path.

### Finding Description
`ExchangeCapsule.transaction()` computes the counter-token amount using the legacy bancor formula (`ExchangeProcessor`) or the hardened `SafeExchangeProcessor`, then updates `firstTokenBalance`/`secondTokenBalance` by simple integer/`BigDecimal` truncation: [1](#0-0) 

Because the buy quantity is truncated (`(long) issuedSupply` / `setScale(0, RoundingMode.DOWN)` in both processors), a sufficiently small `sellTokenQuant` relative to the current reserve ratio can yield `buyTokenQuant` equal to the *entire remaining balance* of the other token, or conversely a series of dust trades can whittle one side's reserve down to exactly `0` without ever reverting on the way there: [2](#0-1) [3](#0-2) 

Once either `firstTokenBalance` or `secondTokenBalance` reaches `0`, `ExchangeTransactionActuator.doValidate()` unconditionally rejects **every future trade on that exchange pair** with "the exchange has been closed": [4](#0-3) 

This is reachable by any account broadcasting an `ExchangeTransactionContract` transaction (no privilege required) via `wallet/broadcasttransaction` gRPC/HTTP, i.e. the exact "broadcast transaction" reachability class called out in the validation rules. `ExchangeInjectActuator` and `ExchangeWithdrawActuator` contain the identical `firstTokenBalance == 0 || secondTokenBalance == 0` guard, so once a pool is driven to zero on one side, none of the three user-facing actuators (trade/inject/withdraw) can recover it: [5](#0-4) 

### Impact Explanation
This matches the report's DoS pattern precisely: an unprivileged, reachable state transition (a trade, not a privileged creation like the Sherlock LP-mint case, but the same "reserve rounds to zero → permanent revert" root cause) can permanently disable the `ExchangeCreate`/`ExchangeInject`/`ExchangeWithdraw`/`ExchangeTransaction` functionality for that specific token pair id. Existing trading positions and balances already withdrawn are unaffected, and other exchange pairs are unaffected, but the specific pool becomes permanently unusable ("closed") since there is no recovery path in the actuators once a balance hits zero. This mirrors the accepted medium-severity conclusion in the Sherlock case: core functionality (trading on that pair) becomes inaccessible, but it does not lock user funds or corrupt global consensus state.

### Likelihood Explanation
Likelihood is low-to-moderate. It requires either (a) a user deliberately submitting a dust `ExchangeTransactionContract` sized to consume exactly the remaining balance of one side (attacker-controlled, deterministic and cheap since `calcFee()` for exchange transactions is `0`), or (b) natural exhaustion of a low-liquidity pool through repeated organic trades approaching zero. Because the truncating math in `ExchangeProcessor`/`SafeExchangeProcessor` makes the exact zero-balance outcome computable in advance, an attacker can reliably target it, similar to how the Sherlock PoC computes the exact price/amount needed to round the pool value to zero.

### Recommendation
- In `ExchangeCapsule.transaction()`, reject (or clamp) any trade whose resulting `newFirstTokenBalance` or `newSecondTokenBalance` would become `0` for the non-hardened path (the hardened path already checks `< 0` but not `== 0`).
- Alternatively, treat a `0` balance in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` as a recoverable "re-seed" state instead of a permanent "closed" state, e.g., allow the original creator to re-inject both sides to bootstrap a fresh ratio.
- Add a minimum-reserve floor (analogous to Uniswap's `MINIMUM_LIQUIDITY`) so that trades which would breach the floor are rejected before committing the new balances.

### Proof of Concept
1. Create a low-liquidity exchange pair via `ExchangeCreateContract`, e.g. `firstTokenBalance = 2`, `secondTokenBalance = large_value` (both above 0, satisfying `doValidate()`'s `> 0` and `balanceLimit` checks in `ExchangeCreateActuator`). [6](#0-5) 
2. Broadcast an `ExchangeTransactionContract` selling `secondToken` for `firstToken` with a `sellTokenQuant` chosen so that `ExchangeProcessor.exchangeFromSupply`/`exchangeToSupply` truncation yields `buyTokenQuant == firstTokenBalance` (i.e., buys out the entire remaining `firstTokenBalance`), driving `newFirstTokenBalance` to exactly `0`: [7](#0-6) 
3. The trade is accepted (`doValidate()` only checks balances are non-zero *before* the trade, and `execute()`/`transaction()` do not reject a resulting balance of exactly `0`).
4. Any subsequent `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract` referencing this `exchange_id` now fails validation with `"Token balance in exchange is equal with 0, the exchange has been closed"`, permanently disabling the pair. [4](#0-3) 

Note: I was unable to fully verify whether any newer/maintenance-triggered process re-seeds or garbage-collects zero-balance exchanges elsewhere in the codebase; my searches did not surface such a mechanism, but this should be confirmed with a full-repo session before treating the DoS as unconditionally permanent.

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-39)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L19-38)
```java
  private BigDecimal exchangeToSupply(long balance, long quant) {
    long newBalance = StrictMathWrapper.addExact(balance, quant);
    BigDecimal bdQuant = BigDecimal.valueOf(quant);
    BigDecimal bdNewBalance = BigDecimal.valueOf(newBalance);
    BigDecimal base = BigDecimal.ONE.add(
        bdQuant.divide(bdNewBalance, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 0.0005);
    return SUPPLY.negate().multiply(
        BigDecimal.ONE.subtract(BigDecimal.valueOf(powResult))).setScale(0, RoundingMode.DOWN);
  }

  private long exchangeFromSupply(long balance, BigDecimal supplyQuant) {
    BigDecimal bdBalance = BigDecimal.valueOf(balance);
    BigDecimal base = BigDecimal.ONE.add(
        supplyQuant.divide(SUPPLY, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 2000.0);
    BigDecimal exchangeBalance = bdBalance.multiply(
        BigDecimal.valueOf(powResult).subtract(BigDecimal.ONE));
    return exchangeBalance.setScale(0, RoundingMode.DOWN).longValueExact();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L200-203)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L201-208)
```java
    if (firstTokenBalance <= 0 || secondTokenBalance <= 0) {
      throw new ContractValidateException("token balance must greater than zero");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```
