### Title
Exchange liquidity inject/withdraw operations compute settlement amounts solely from the instantaneous on-chain pool ratio with no caller-specified slippage/minimum bound, enabling sandwich attacks - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
`ExchangeInjectActuator` and `ExchangeWithdrawActuator` derive the counter-token amount (`anotherTokenQuant`) purely from the current, mutable `firstTokenBalance`/`secondTokenBalance` ratio stored in `ExchangeCapsule` at the moment of execution, with no caller-supplied minimum/maximum bound. This is the same root-cause pattern as the reported issue: a settlement/pricing value is computed exclusively from a "spot" state value (there an oracle price, here the AMM pool ratio) without any slippage-tolerance parameter, making it exploitable via sandwich attacks around the pending transaction.

### Finding Description
In `ExchangeInjectActuator.execute`/`doValidate`, `anotherTokenQuant` is computed as a straight proportional value of the pool balances at execution time: [1](#0-0) 
and validated the same way in `doValidate`: [2](#0-1) 

Similarly, `ExchangeWithdrawActuator` computes and validates `anotherTokenQuant` purely from the live pool ratio: [3](#0-2) [4](#0-3) 

Neither `ExchangeInjectContract` nor `ExchangeWithdrawContract` carries a slippage/minimum-expected field. This contrasts directly with `ExchangeTransactionContract`, which does include an `expected` field and enforces it before completing a swap: [5](#0-4) 

The pool ratio itself is trivially and permissionlessly manipulable by anyone through `ExchangeTransactionContract`, which calls `ExchangeCapsule.transaction()` (Bancor-style AMM math) and directly mutates `firstTokenBalance`/`secondTokenBalance`: [6](#0-5) 

Because Tron transactions are broadcast to the public mempool before block packing, an unprivileged, anonymous attacker can observe a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction from the exchange creator and sandwich it: (1) submit an `ExchangeTransactionContract` trade to skew the pool ratio just before the target transaction executes, (2) let the inject/withdraw settle at the skewed ratio (since it has no minimum/maximum protection), (3) submit a reverse `ExchangeTransactionContract` trade to restore the ratio and capture the value extracted from the asymmetric injection/withdrawal.

### Impact Explanation
The exchange creator, whose funds and pool shares are directly at risk, can be forced into contributing/withdrawing tokens at a manipulated ratio rather than the fair pool ratio, directly transferring value to the attacker who performs the sandwiching trades. Because both `execute` paths mutate `ExchangeCapsule` balances and account asset/TRX balances (`AccountCapsule.reduceAssetAmountV2`/`addAssetAmountV2`, `setBalance`) based on this manipulated ratio, this constitutes real, unrecoverable asset/accounting loss, matching the "asset or accounting corruption" acceptance criteria.

### Likelihood Explanation
Exploitation requires no privileged role or leaked key: any account can submit `ExchangeTransactionContract` transactions against a public exchange, and transaction ordering within the mempool/block is influenced by fee/energy bidding and network propagation timing, which is a well-known sandwich vector on Tron just as on other chains. The only precondition is that the target account issues an `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction, which is a normal, expected operation for exchange creators managing liquidity.

### Recommendation
Add a caller-specified minimum/maximum bound (slippage tolerance) to `ExchangeInjectContract` and `ExchangeWithdrawContract`, analogous to the `expected` field already present in `ExchangeTransactionContract`, and enforce it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` before committing balance changes, so that inject/withdraw operations abort if the computed `anotherTokenQuant` falls outside the caller's tolerance.

### Proof of Concept
1. Attacker monitors the mempool for a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) from the exchange creator for exchange `X` with tokens `A`/`B`.
2. Attacker submits `ExchangeTransactionContract` selling a large amount of `A` into exchange `X`, shifting `firstTokenBalance`/`secondTokenBalance` heavily in favor of `A` (via `ExchangeCapsule.transaction` → `ExchangeProcessor.exchange`), and pays higher fee/energy to get it ordered before the creator's transaction.
3. The creator's `ExchangeInjectContract` executes; `anotherTokenQuant` in `ExchangeInjectActuator.execute` (lines 71-83) is computed from the now-skewed ratio, causing the creator to inject a disproportionate amount of `B` relative to fair value.
4. Attacker submits a reverse `ExchangeTransactionContract` (buying back `A`) immediately after, restoring the ratio and realizing a profit equal to the value transferred out of the creator's injected liquidity.
5. The same pattern applies to `ExchangeWithdrawContract`, where the attacker can front-run to shift the ratio so the withdrawal returns a disadvantageous mix of `A`/`B` to the creator, then reverses the trade for profit.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-227)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenID = secondTokenID;
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divide(bigFirstTokenBalance).longValueExact();
      newTokenBalance = addExact(firstTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(secondTokenBalance, anotherTokenQuant);
    } else {
      anotherTokenID = firstTokenID;
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divide(bigSecondTokenBalance).longValueExact();
      newTokenBalance = addExact(secondTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(firstTokenBalance, anotherTokenQuant);
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-247)
```java
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

    } else {
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigSecondTokenBalance).longValueExact();
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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
