## Title
Exchange (TRC10 bancor-AMM) inject/withdraw actuators compute settlement amounts from unprotected instantaneous pool balances, enabling sandwich-based DoS/value-extraction analogous to Beanstalk's Flood frontrunning issue - (`actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
The Beanstalk report describes a mechanism that derives a critical, value-moving quantity (`newBeans`) directly from the instantaneous state of an AMM pool with no oracle/TWAP protection, letting an unprivileged frontrunner sandwich the triggering transaction to zero-out or corrupt the computed value and DoS/grief the mechanism. java-tron's on-chain TRC10↔TRX/TRC10 "Exchange" (bancor-style AMM) actuators exhibit the same root-cause pattern: `ExchangeInjectActuator` and `ExchangeWithdrawActuator` compute the counter-token amount purely from the exchange's current spot balances (`getFirstTokenBalance()`/`getSecondTokenBalance()`), with no oracle, no TWAP, and (for Inject) no caller-supplied bound/slippage parameter at all.

### Finding Description
`ExchangeInjectActuator.execute` recomputes `anotherTokenQuant` at execution time directly from the exchange's live balances: [1](#0-0) 

This mirrors `Weather::sop`'s direct, un-cushioned read of the Curve metapool's instantaneous balances via `getDeltaB()`. Just as an attacker can sandwich `SeasonFacet::gm` to move `deltaB` to ≤0 before the beneficial mint/sell executes, an unprivileged actor can submit swap transactions (via `ExchangeTransactionActuator`, reachable by any broadcast transaction) immediately ahead of a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction to skew `firstTokenBalance`/`secondTokenBalance`, then reverse the swap afterward. Because the injected/withdrawn counter-token amount is a direct linear function of the momentarily-skewed ratio, and:
- `ExchangeInjectActuator` has **no caller-specified minimum/maximum bound** on `anotherTokenQuant` (unlike `ExchangeTransactionActuator`, which at least checks `tokenExpected`): [2](#0-1) 
- The only downstream guards are balance-sufficiency and a global `ExchangeBalanceLimit` check, both of which can be tripped by the manipulated ratio, causing the victim's transaction to revert with `"balance is not enough"` or `"token balance must less than ..."`.

`ExchangeWithdrawActuator` computes the same ratio-derived amount and additionally enforces a hard precision tolerance (`"Not precise enough"`) computed from the same manipulable spot balances: [3](#0-2) 
An attacker can shift the ratio just enough (within precision constraints they control by tuning their own sandwich amount) to push the recomputed `anotherTokenQuant` outside the withdrawer's expected precision window, causing the legitimate transaction to revert.

Both actuators use the same `ExchangeProcessor`/`SafeExchangeProcessor` bancor math against raw, unweighted, single-block-observable balances: [4](#0-3) 
There is no time-weighted or oracle-based safeguard anywhere in this code path, exactly the deficiency identified in the Beanstalk report relative to `Oracle::stepOracle`'s TWAP `deltaB`.

### Impact Explanation
An unprivileged, anonymous actor observing a pending `ExchangeInjectContract` or `ExchangeWithdrawContract` transaction in the transaction pool can sandwich it with ordinary `ExchangeTransactionContract` swaps (front-run + back-run) to:
- Force the victim's liquidity-provision/withdrawal transaction to revert (DoS on Exchange liquidity operations), or
- Force settlement at an artificially skewed ratio, causing economic loss to the exchange creator performing the inject/withdraw.
This is a denial-of-service / value-extraction primitive against the on-chain Exchange feature, reachable purely via ordinary broadcast transactions, with no privileged access required by the attacker.

### Likelihood Explanation
The attack only requires funds sufficient to move the pool's spot ratio and pay transaction fees, and visibility of the pending transaction (standard mempool/broadcast visibility). No special network position, key material, or validator privilege is required, making this readily exploitable by any economically motivated MEV-style actor, similar to the low-cost (0.08% of funds) sandwich described in the source report.

### Recommendation
- Add caller-specified bound parameters (minimum/maximum counter-token amount) to `ExchangeInjectContract`/`ExchangeWithdrawContract`, similar to the `tokenExpected` slippage guard already present in `ExchangeTransactionContract`, so users can protect themselves against ratio manipulation between submission and execution.
- Consider deriving critical amounts from a time-weighted average of the exchange balances rather than the instantaneous spot balances, consistent with the Beanstalk report's recommendation to use a TWAP-based value in place of a spot-derived one.

### Proof of Concept
1. Exchange `E` holds `firstTokenBalance = A`, `secondTokenBalance = B` (TRX/TRC10 pair).
2. Creator broadcasts `ExchangeInjectContract` specifying `tokenID = first`, `tokenQuant = Q` (no bound on the resulting `anotherTokenQuant`).
3. Attacker observes this pending transaction and broadcasts a large `ExchangeTransactionContract` swap that shifts `A`/`B` before the creator's transaction is packed, causing `anotherTokenQuant = floorDiv(B' * Q, A')` (computed in `ExchangeInjectActuator.execute`, lines 73-82) to be far larger/smaller than the creator anticipated when they signed the transaction.
4. The creator's transaction either reverts (insufficient balance / exceeds `ExchangeBalanceLimit`) — a DoS on their liquidity operation — or succeeds while injecting a disadvantageous ratio, transferring value to the attacker who then reverses their swap to restore the ratio and pocket the difference.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-83)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L229-236)
```java
    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-243)
```java
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
      if (allowHarden) {
        BigDecimal remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
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
