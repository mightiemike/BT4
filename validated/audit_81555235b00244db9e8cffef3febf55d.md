### Title
Missing slippage/minimum-output protection in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` (TRC10 exchange liquidity operations) - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java])

### Summary
The `ExchangeInjectContract` and `ExchangeWithdrawContract` messages (and their actuators) implement liquidity provision/removal on TRON's built-in bancor-style TRC10 exchange, directly analogous to `enter()`/`quit()` in the reported BorpaGateway contract. Unlike `ExchangeTransactionContract`, which carries an `expected` field enforced in `ExchangeTransactionActuator.doValidate()` as a minimum-output guard, `ExchangeInjectContract` and `ExchangeWithdrawContract` have no user-supplied minimum/expected parameter at all.

### Finding Description
`ExchangeTransactionContract` explicitly includes an `expected` field used as slippage protection: [1](#0-0) , and it is enforced in `ExchangeTransactionActuator.doValidate()` via `if (anotherTokenQuant < tokenExpected) throw ...` [2](#0-1) .

In contrast, `ExchangeInjectContract` only carries `owner_address`, `exchange_id`, `token_id`, `quant` [3](#0-2) , and `ExchangeWithdrawContract` is likewise limited to `owner_address`, `exchange_id`, `token_id`, `quant` [4](#0-3) . Neither carries any caller-specified minimum acceptable counter-token amount.

`ExchangeInjectActuator.doValidate()`/`execute()` computes `anotherTokenQuant` purely from the exchange pool ratio present at validation/execution time and deducts exactly that amount from the account, with no way for the caller to bound it: [5](#0-4) . Similarly, `ExchangeWithdrawActuator.doValidate()`/`execute()` computes the counter-token amount to be paid out from the current pool ratio, again with no minimum-received check: [6](#0-5) . The only checks present (`"Not precise enough"`) validate the internal rounding precision of the ratio math, not that the ratio is acceptable to the user given possible front-running.

Because the pool ratio (`firstTokenBalance`/`secondTokenBalance`) can be moved by any other `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract` transaction ordered before this one within the same block (transaction ordering/front-running is influenced by broadcasting/relaying and packing order, not guaranteed FIFO), a withdrawing or injecting account can receive/pay a significantly different counter-token amount than expected when they signed the transaction — exactly the missing-slippage-protection pattern described in the report for `enter()`/`quit()`.

### Impact Explanation
An exchange creator (the only party authorized to call inject/withdraw, per the `accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())` check) who submits an `ExchangeInjectContract` or `ExchangeWithdrawContract` transaction can be front-run by any other account interacting with the same TRC10 exchange pair (via `ExchangeTransactionContract`), causing the injector/withdrawer to receive/pay counter-token amounts far worse than the ratio they observed off-chain, resulting in direct value loss. This is an accounting/exchange-math corruption of expectations, matching the report's "unexpected losses due to unfavorable... rates" impact class.

### Likelihood Explanation
Any user can broadcast an `ExchangeTransactionContract` against a public TRC10 exchange pair at any time, and transaction ordering within a block is influenced by network propagation/miner packing rather than strict submission order, making front-running of a known pending inject/withdraw transaction practically achievable by an unprivileged observer of the mempool/broadcast network.

### Recommendation
Add a caller-specified minimum acceptable counter-token amount (analogous to `expected` in `ExchangeTransactionContract`) to `ExchangeInjectContract` and `ExchangeWithdrawContract`, and enforce it in `ExchangeInjectActuator.doValidate()` and `ExchangeWithdrawActuator.doValidate()` before allowing the operation to proceed, mirroring the existing `tokenExpected` check in `ExchangeTransactionActuator`.

### Proof of Concept
1. Attacker monitors the mempool/broadcast network for a pending `ExchangeWithdrawContract` (or `ExchangeInjectContract`) from the exchange creator targeting exchange `id`.
2. Attacker submits an `ExchangeTransactionContract` against the same `exchange_id` that shifts `firstTokenBalance`/`secondTokenBalance` unfavorably, and ensures it is packed before the victim's transaction.
3. The victim's `ExchangeWithdrawActuator.execute()` (or `ExchangeInjectActuator.execute()`) computes `anotherTokenQuant` from the now-skewed ratio [7](#0-6) , causing the victim to receive/pay an amount worse than expected, with no on-chain mechanism to reject the transaction for insufficient output — unlike `ExchangeTransactionActuator`, which would reject via the `expected` check.

### Citations

**File:** Tron protobuf protocol document.md (L1384-1401)
```markdown
     - message `ExchangeInjectContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to inject.
    
       `quant`: token amount to inject.
    
      ```java
      message ExchangeInjectContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
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

**File:** Tron protobuf protocol document.md (L1422-1441)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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
