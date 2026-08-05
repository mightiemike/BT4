Confirmed: `ExchangeTransactionContract` has an `expected` field (line 36) for slippage protection, but `ExchangeInjectContract` and `ExchangeWithdrawContract` (lines 17-29) have no such field. [1](#0-0) 

### Title
Missing slippage protection in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` enables sandwich-style pool re-ratio manipulation - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
TRON's built-in bancor-style token exchange lets any account create a pool (`ExchangeCreateContract`) and, as the pool's `creatorAddress`, inject or withdraw liquidity via `ExchangeInjectContract`/`ExchangeWithdrawContract`. Both actuators compute the counterpart-token amount (`anotherTokenQuant`) proportionally from the pool's **live** `firstTokenBalance`/`secondTokenBalance` at validation/execution time, exactly mirroring the reported bug class: "the pool adjusts its composition based on a specified amount of tokens" using prices that can change between when the transaction is built and when it executes. Unlike `ExchangeTransactionContract` (swaps), which carries an `expected` field enforced in `ExchangeTransactionActuator.doValidate()` as slippage protection, `ExchangeInjectContract`/`ExchangeWithdrawContract` carry no such bound. [2](#0-1) [3](#0-2) 

### Finding Description
`ExchangeInjectActuator.execute()` re-derives `anotherTokenQuant` from the current on-chain pool ratio at the moment the block-producing node processes the transaction: [4](#0-3) 

```java
if (Arrays.equals(tokenID, firstTokenID)) {
  anotherTokenID = secondTokenID;
  anotherTokenQuant = floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance);
  exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
      addExact(secondTokenBalance, anotherTokenQuant));
} ...
```

`ExchangeWithdrawActuator.execute()` does the same for the withdrawal amount using `bigFirstTokenBalance`/`bigSecondTokenBalance` at execution time. [5](#0-4) 

Neither contract carries a caller-supplied floor/ceiling on `anotherTokenQuant` (no `expected` field, unlike the swap contract). [6](#0-5)  `ExchangeWithdrawActuator.doValidate()`'s "precise enough" check at lines 228-243 only reconciles rounding drift between the validate-time and execute-time computation of the *same* ratio — it is not a price/slippage bound and does not protect against the ratio itself shifting. [7](#0-6) 

Because the exchange pool's `firstTokenBalance`/`secondTokenBalance` can be altered by any prior `ExchangeTransactionContract` (a swap that any unprivileged account can submit) executed earlier in the same block or in blocks between when the LP built and broadcast their inject/withdraw transaction, an attacker can:
1. Observe a pending/likely inject or withdraw transaction from the pool creator.
2. Submit a swap (`ExchangeTransactionActuator`) that shifts `firstTokenBalance`/`secondTokenBalance` just before the LP's transaction is packed into a block.
3. Let the victim's inject/withdraw execute against the now-skewed ratio — as `ExchangeCapsule.transaction()` and the Inject/Withdraw ratio math both read the same mutable `firstTokenBalance`/`secondTokenBalance` fields on `ExchangeCapsule`. [8](#0-7) 
4. Reverse the swap afterward, extracting value at the LP's expense (classic sandwich attack), forcing the pool creator to either inject a disadvantageous amount of the counterpart token or withdraw less than intended, without any on-chain limit rejecting the trade.

This is the direct analog of the reported "Potential Pool Unbalancing" issue: the fix suggested there — enforce a bound on the rebalancing amount / avoid recomputation purely from stale-vs-current price — is precisely what `ExchangeTransactionContract.expected` already provides for swaps but is absent for inject/withdraw.

### Impact Explanation
This is an accounting/settlement impact: the pool creator's asset balances are transferred at an attacker-manipulable exchange ratio instead of the ratio they intended when signing the transaction, with the actuator offering no on-chain floor to reject an adverse rate. This can result in economic loss to the pool's liquidity provider and a genuine on-chain composition/balance divergence from what the transaction author authorized, matching the "underpriced-public-work"/settlement-manipulation class named in scope.

### Likelihood Explanation
Any unprivileged account can create an exchange (`ExchangeCreateContract`) and become its "creator" — this is a self-assigned role, not a system-trusted role — then submit inject/withdraw transactions, so the affected code path is reachable by ordinary users. An attacker only needs to observe a pending inject/withdraw transaction (visible before block inclusion) and race a swap ahead of it, which is standard MEV/front-running feasible on TRON's public mempool.

### Recommendation
Add an `expected`/limit field to `ExchangeInjectContract` and `ExchangeWithdrawContract`, mirroring `ExchangeTransactionContract.expected`, and enforce it in `ExchangeInjectActuator.doValidate()`/`execute()` and `ExchangeWithdrawActuator.doValidate()`/`execute()` so the actuator rejects execution if the computed `anotherTokenQuant` falls outside the caller's specified bound, preventing silent re-balancing at a stale/manipulated ratio.

### Proof of Concept
1. Pool creator `A` submits `ExchangeInjectContract` intending to add `tokenQuant` of `firstToken`, expecting to also contribute `anotherTokenQuant` of `secondToken` computed from the current ratio `firstTokenBalance : secondTokenBalance`.
2. Attacker `B` submits `ExchangeTransactionContract` selling a large amount of `firstToken` into the pool just before `A`'s transaction is included, shifting `firstTokenBalance`/`secondTokenBalance` via `ExchangeCapsule.transaction()`. [9](#0-8) 
3. `A`'s `ExchangeInjectContract` executes against the new, skewed balances, computing a much larger `anotherTokenQuant` than `A` intended (per `ExchangeInjectActuator.execute()` lines 71-83), draining more of `A`'s `secondToken` balance than expected, with no `expected`-style check to abort.
4. Attacker `B` reverses their swap, restoring the ratio and extracting the value transferred from `A`'s inject transaction.

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-37)
```text
message ExchangeInjectContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeWithdrawContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeTransactionContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
  int64 expected = 5;
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-231)
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

    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }
```

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L106-112)
```java
  public long getFirstTokenBalance() {
    return this.exchange.getFirstTokenBalance();
  }

  public long getSecondTokenBalance() {
    return this.exchange.getSecondTokenBalance();
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
