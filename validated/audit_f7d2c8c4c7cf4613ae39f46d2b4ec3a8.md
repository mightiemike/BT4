## Title
Lack of slippage protection in `ExchangeWithdrawContract` allows liquidity providers to receive fewer tokens than expected due to pool ratio changes before transaction execution - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
java-tron implements a Bancor-style relay AMM ("Exchange") where a creator can inject and withdraw liquidity for a TRC10 token pair. Unlike `ExchangeTransactionContract` (trades), which carries an `expected` minimum-output field for slippage protection, `ExchangeWithdrawContract` has no such minimum-output parameter. The paired-token amount a withdrawer receives is computed from the pool's *current* balances at execution time, so if the ratio shifts between signing and inclusion (e.g., a trade executes first), the withdrawer can silently receive less of the paired asset than they expected — the exact bug class described in the Sherlock report for `Pool.sol`/`SuperPool.sol` withdrawals.

### Finding Description
`ExchangeWithdrawContract` only specifies the `token_id` and `quant` of one side of the pair to withdraw; there is no field for a minimum acceptable amount of the other token, as confirmed in the protobuf spec: [1](#0-0) 

Contrast this with `ExchangeTransactionContract`, which explicitly includes an `expected` field precisely to protect against unfavorable rate changes: [2](#0-1) 

In `ExchangeTransactionActuator`, that `expected` value is enforced during validation: [3](#0-2) 

By contrast, `ExchangeWithdrawActuator` computes `anotherTokenQuant` purely from the pool's live `firstTokenBalance`/`secondTokenBalance` ratio at execution time with no user-supplied floor: [4](#0-3) 

The only check present in `doValidate()` is a rounding/precision check ("Not precise enough") comparing the freshly computed amount against itself — it is not a user-specified slippage bound: [5](#0-4) 

This mirrors the reported root cause in `Pool.sol`/`SuperPool.sol`: a withdrawal function that burns/receives an amount computed from a mutable, dynamically-derived exchange rate at execution time, with no way for the caller to bound the outcome.

### Impact Explanation
Anyone can create an Exchange pool via `ExchangeCreateContract` and later act as its creator (the only account authorized to call `ExchangeWithdrawContract`, per the creator check at `ExchangeWithdrawActuator.java:181`). If, between the time the withdraw transaction is signed/broadcast and the time it is included in a block, another party executes an `ExchangeTransactionContract` trade against the same pool (front-running or natural mempool activity), the pool ratio shifts and the withdrawer receives a different (potentially much smaller) amount of the paired token than they intended — a direct, concrete loss of funds for the withdrawing user, with no recourse since no minimum-received guard exists.

### Likelihood Explanation
This is trivially reachable by any unprivileged user with an Exchange pool: no privileged role is required, and pool ratio manipulation only requires submitting an ordinary `ExchangeTransactionContract` trade ahead of the pending withdrawal in the same or an intervening block — a standard MEV/front-running pattern already anticipated and defended against for trades (via `expected`) but not for withdrawals.

### Recommendation
Add a minimum-expected-amount field (analogous to `expected` in `ExchangeTransactionContract`) to `ExchangeWithdrawContract`, and enforce it in `ExchangeWithdrawActuator.doValidate()`/`execute()` by rejecting the withdrawal if the computed `anotherTokenQuant` falls below the caller-specified floor.

### Proof of Concept
1. Creator establishes an Exchange pool with `firstTokenBalance = 2000`, `secondTokenBalance = 2000` and signs an `ExchangeWithdrawContract` for `tokenQuant = 500` of `firstToken`, expecting `anotherTokenQuant ≈ 500` of `secondToken`.
2. Before this transaction lands, someone submits an `ExchangeTransactionContract` that shifts the pool to `firstTokenBalance = 2000`, `secondTokenBalance = 1500`.
3. When the withdraw transaction executes, `ExchangeWithdrawActuator.execute()` computes `anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance = 1500 * 500 / 2000 = 375`, far less than the creator anticipated, per the computation at `ExchangeWithdrawActuator.java:79-80`, with no validation step able to reject this unfavorable outcome.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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
