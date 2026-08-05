### Title
Manipulable Bancor-style Exchange reserve ratio allows creators to extract disproportionate value on withdrawal - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java])

### Summary
The TRC10↔TRX Bancor-style AMM pools (`ExchangeCapsule`/`Exchange*Actuator` family) compute withdrawal (and injection) payouts purely from the *instantaneous* on-chain reserve ratio, with no protection against an exchange creator first skewing that ratio via a self-executed swap (`ExchangeTransactionContract`) and then withdrawing liquidity at the manipulated ratio — the same root-cause pattern as the Sherlock report's `FluidLocker.sol::withdrawLiquidity` bug, which lacked any check against price manipulation before paying out liquidity.

### Finding Description
Any account can create an Exchange pool (`ExchangeCreateContract`) pairing TRX with a TRC10 token, becoming its "creator." Only the creator may call `ExchangeInjectContract`/`ExchangeWithdrawContract` [1](#0-0) , but *any* account (not just the creator) can trade against the pool via `ExchangeTransactionContract`, which mutates the pool's reserves through `ExchangeCapsule.transaction()` using a constant-product (Bancor) formula [2](#0-1) . Trading via `ExchangeTransactionActuator` carries **zero protocol fee** (`calcFee()` returns 0) [3](#0-2) , so a creator can cheaply swap to push the reserve ratio to an extreme.

`ExchangeWithdrawActuator` then computes the paired ("another") token amount to return strictly from the *current* `firstTokenBalance`/`secondTokenBalance` ratio at execution time, with only a precision-consistency check, not a manipulation/TWAP check: [4](#0-3) [5](#0-4) 

There is no oracle/TWAP reference, no minimum-liquidity requirement, and no restriction preventing a withdrawal from occurring immediately after a large self-trade skews the ratio — exactly the missing control identified in the external report ("no check against price manipulation" in the withdraw path).

### Impact Explanation
Because payout is derived from a spot ratio that the withdrawing party itself can move via a fee-free swap immediately beforehand, the creator can extract a larger share of the pool's real value than their true proportional contribution, at the expense of the exchange's remaining reserves (and thus any subsequent traders using `ExchangeTransactionContract` against that pool, who face a drained/imbalanced pool and worse effective pricing/slippage). This is an accounting/settlement flaw: withdrawal amounts diverge from the economically correct proportional share, mirroring the underpriced/incorrect settlement class of impact from the original report.

### Likelihood Explanation
Exchange creation, trading, injection, and withdrawal are all unprivileged operations reachable by any account with sufficient TRX/token balance; no committee or witness permission is required. The only requirement is that the pool have "low enough" liquidity for the ratio to be moved cheaply (same external precondition as the source report), which is common for TRC10/TRX pools created by ordinary users. The attack requires only sequential transactions (swap → withdraw), no special tooling.

### Recommendation
Add a manipulation-resistance check to `ExchangeWithdrawActuator` (and `ExchangeInjectActuator`), e.g., comparing the current ratio against a time-weighted/short-history average, rejecting withdrawals when the ratio has moved beyond a bounded threshold within a recent window, or enforcing a cool-down between a same-account trade and withdrawal on the same exchange.

### Proof of Concept
1. Attacker calls `ExchangeCreateContract` to create pool P(TRX, TokenA) and becomes creator; injects modest liquidity via `ExchangeInjectContract`.
2. Attacker calls `ExchangeTransactionContract` to sell a large amount of TRX into P, skewing reserves so TokenA becomes scarce relative to TRX (fee-free per `calcFee()==0`) [6](#0-5) .
3. Attacker calls `ExchangeWithdrawContract` specifying `token_id = TokenA`, `quant = <small amount>`; because reserves are skewed, `anotherTokenQuant` (TRX returned) computed at lines 74-89 of `ExchangeWithdrawActuator.java` is disproportionately large relative to the attacker's original deposit.
4. Attacker reverses the initial swap (buy back TRX with TokenA) to restore the ratio, pocketing the excess TRX extracted during the skewed withdrawal — net value drained from the pool's genuine reserves.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
```java
      BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
      BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
      BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L228-243)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-76)
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

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L232-235)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```
