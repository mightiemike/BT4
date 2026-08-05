## Analysis

The Cork Protocol bug is about missing minimum-output slippage checks (`amountAMin`/`amountBMin` = 0) when removing liquidity/swapping in an AMM. `java-tron` has its own Bancor-formula AMM subsystem — the `Exchange` contracts (`ExchangeCreateContract`, `ExchangeInjectContract`, `ExchangeWithdrawContract`, `ExchangeTransactionContract`). Notably, the swap path (`ExchangeTransactionContract`) already includes an `expected` (min-out) field that is checked in `ExchangeTransactionActuator.doValidate()` [1](#0-0) , so the "swap" half of the Cork bug is already mitigated in java-tron.

However, the liquidity-removal path (`ExchangeWithdrawContract`/`ExchangeWithdrawActuator`) has no such minimum-output protection — this is the direct analog of Cork's unprotected `removeLiquidity(..., 0, 0, ...)` call.

### Title
Lack of slippage protection in TRC10 AMM liquidity withdrawal - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
`ExchangeWithdrawContract` allows an exchange creator to withdraw `tokenQuant` of one token from a Bancor-style TRC10 `Exchange` pool, with `anotherTokenQuant` computed proportionally from the *current* pool ratio at execution time. There is no user-supplied minimum for `anotherTokenQuant`, so if the pool ratio is shifted between transaction submission and execution, the withdrawer can receive materially less of the paired token than expected — mirroring the unprotected `removeLiquidity(..., 0, 0, ...)` call in the Cork report.

### Finding Description
`ExchangeWithdrawContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` [2](#0-1)  — there is no `expected`/minimum-output field, unlike `ExchangeTransactionContract` which explicitly carries an `expected` field for this purpose [3](#0-2) .

In `ExchangeWithdrawActuator.execute()`, `anotherTokenQuant` is derived from the exchange's current balances at execution time using simple proportional math, and is applied without comparison to any caller-supplied floor: [4](#0-3) 

The `doValidate()` method similarly only checks that `anotherTokenQuant` is `> 0` and that the exchange has sufficient balance and precision — it never checks it against a minimum acceptable amount supplied by the withdrawer: [5](#0-4) 

Because any unprivileged user can submit an `ExchangeTransactionContract` swap against the same `exchange_id` (the swap actuator directly mutates `firstTokenBalance`/`secondTokenBalance` via `ExchangeCapsule.transaction()` [6](#0-5) ), an attacker can shift the pool ratio by executing a swap that lands in the same or an earlier block than a pending withdrawal, before the withdrawal transaction executes. This is directly analogous to the Cork Protocol case where `removeLiquidity` was called with `amountAMin = amountBMin = 0`, allowing an attacker to manipulate price via frontrunning and force the protocol/user to bear the full unbounded slippage on liquidity removal.

### Impact Explanation
An exchange creator withdrawing liquidity via `ExchangeWithdrawContract` can receive substantially less of the paired token than the pool ratio implied at the time they signed/broadcast the transaction, with no on-chain mechanism to guarantee a minimum. This is a direct loss-of-funds vector for the withdrawer, matching the "loss of protocol/user funds" impact category from the original report (accounting/settlement of an AMM-like exchange primitive).

### Likelihood Explanation
Exploitation requires only unprivileged actions: broadcasting an `ExchangeTransactionContract` swap against the target `exchange_id` timed to execute before the victim's `ExchangeWithdrawContract`. Given TRON's block production and transaction ordering is controlled by Super Representatives (and swap transactions are cheap, permissionless, and freely composable against any exchange id), this is a realistically reachable griefing/extraction pattern, though it depends on timing/mempool visibility rather than being deterministically guaranteed on every attempt.

### Recommendation
Add a caller-supplied minimum-output field (analogous to `expected` in `ExchangeTransactionContract`) to `ExchangeWithdrawContract` (and consider the same for `ExchangeInjectContract`), and enforce it in `ExchangeWithdrawActuator.doValidate()`/`execute()` by rejecting the withdrawal if the computed `anotherTokenQuant` falls below the caller's specified minimum.

### Proof of Concept
1. Attacker observes a pending `ExchangeWithdrawContract` from victim V targeting `exchange_id = X`, withdrawing `tokenQuant` of `firstTokenID`.
2. Attacker broadcasts an `ExchangeTransactionContract` against the same `exchange_id = X`, selling a large amount of `secondTokenID` to shift `firstTokenBalance`/`secondTokenBalance` unfavorably for V, timed to be processed before V's withdrawal.
3. When V's `ExchangeWithdrawActuator.execute()` runs, `anotherTokenQuant` is computed from the now-skewed balances [4](#0-3) , delivering V a lower amount of the paired token than the ratio at signing time, with no minimum-output check to abort the transaction.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** Tron protobuf protocol document.md (L1403-1420)
```markdown
     - message `ExchangeWithdrawContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to withdraw.
    
       `quant`: token amount to withdraw.
    
      ```java
      message ExchangeWithdrawContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
```

**File:** Tron protobuf protocol document.md (L1422-1442)
```markdown
     - message `ExchangeTransactionContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to sell.
    
       `quant`: token amount to sell.
    
       `expected`: expected minimum number of tokens.
    
      ```java
      message ExchangeTransactionContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
          int64 expected = 5;
      }
      ```
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-254)
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
      if (secondTokenBalance < tokenQuant || firstTokenBalance < anotherTokenQuant) {
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
