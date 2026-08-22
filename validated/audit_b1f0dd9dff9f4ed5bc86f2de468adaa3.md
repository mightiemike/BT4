This confirms the analog: `ExchangeWithdrawContract` has no minimum-output/slippage field, while `ExchangeTransactionContract` was fixed with an `expected` field (`protocol/src/main/protos/core/contract/exchange_contract.proto` lines 24-37).

### Title
Missing slippage protection in `ExchangeWithdrawActuator` allows liquidity withdrawers to receive fewer tokens than expected - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
`ExchangeWithdrawContract`/`ExchangeWithdrawActuator` computes the "other side" token amount to return to the withdrawer strictly from the pool ratio at execution time, with no user-supplied minimum-acceptable-amount (slippage tolerance) parameter. In contrast, `ExchangeTransactionContract`/`ExchangeTransactionActuator` was hardened with an `expected` field that lets the caller specify a minimum acceptable output and reverts the trade otherwise. The withdraw path never received the equivalent protection.

### Finding Description
`ExchangeWithdrawContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no field equivalent to the `expected` field present in `ExchangeTransactionContract`. [1](#0-0) 

In `ExchangeWithdrawActuator.execute()`, `anotherTokenQuant` (the counter-asset amount paid out to the withdrawer) is derived purely from the exchange's current `firstTokenBalance`/`secondTokenBalance` ratio and the requested `tokenQuant`, at the moment the transaction is actually executed in a block — not at the moment the user signed/broadcast it: [2](#0-1) 

`doValidate()` only checks that the computed `anotherTokenQuant` is internally consistent/precise enough (the "Not precise enough" rounding check) and that pool balances are sufficient — it never compares the result against any caller-supplied minimum: [3](#0-2) 

This is the exact bug class described in the external report: `deposit()`-equivalent (`ExchangeTransactionActuator`, trading into the pool) was fixed by adding a caller-supplied `expected` minimum that is validated against `exchangeCapsule.transaction(...)`: [4](#0-3) 
but `requestWithdraw()`-equivalent (`ExchangeWithdrawActuator`, pulling liquidity back out) was left unprotected.

Because the pool ratio (`firstTokenBalance`/`secondTokenBalance`) can be moved by any number of intervening `ExchangeTransactionContract` trades between the time a withdrawer constructs/signs a withdraw transaction and the time it is actually packed into a block, the amount ultimately received can differ arbitrarily from what the withdrawer expected when signing, with no on-chain mechanism to reject an unfavorable execution.

### Impact Explanation
A liquidity withdrawer using `ExchangeWithdrawContract` has no way to bound the worst-case counter-asset amount they will receive. An adversary (or normal market activity) can shift the pool ratio via `ExchangeTransactionContract` trades executed in the same block or immediately before the withdraw transaction is confirmed, causing the withdrawer to receive a significantly devalued amount of the counter asset relative to what the pool looked like when they signed the transaction. This is a concrete asset/accounting-correctness issue reachable by any anonymous account broadcasting a transaction, matching the "asset or accounting corruption" acceptance criterion.

### Likelihood Explanation
Any account that is the creator of an exchange (the only actor authorized to withdraw, per the `is not creator` check) routinely calls `ExchangeWithdrawContract`, and any other account can submit `ExchangeTransactionContract` trades against the same exchange pool in the intervening blocks/same block ordering, since exchange trading is open to all accounts. No special privileges beyond being the pool creator are required to be exposed, and no special privileges are required to move the pool ratio against them, making this readily triggerable during normal chain operation or via deliberate front-running/sandwiching of a pending withdraw transaction.

### Recommendation
Add an `expected`/minimum-output field to `ExchangeWithdrawContract` (mirroring `ExchangeTransactionContract.expected`), and in `ExchangeWithdrawActuator.doValidate()`/`execute()` reject the transaction (e.g., "token required must greater than expected") if the computed `anotherTokenQuant` falls below the caller-supplied minimum, exactly as already done for `ExchangeTransactionActuator`.

### Proof of Concept
1. Account A creates an exchange pool via `ExchangeCreateContract` and is its creator.
2. Account A signs and broadcasts an `ExchangeWithdrawContract` transaction to withdraw `tokenQuant` of `firstTokenID`, expecting to receive a certain amount of `secondTokenID` based on the pool ratio at signing time.
3. Before A's transaction is included, other accounts submit `ExchangeTransactionContract` trades against the same `exchange_id` that shift `firstTokenBalance`/`secondTokenBalance` unfavorably for A's pending withdrawal (e.g., large trade in the direction that depresses the ratio A relies on).
4. When A's `ExchangeWithdrawContract` is finally executed, `ExchangeWithdrawActuator.execute()` (lines 74-89) computes `anotherTokenQuant` from the now-shifted balances with no lower bound check, and A receives materially less value than anticipated with the transaction still reporting `code.SUCESS` — there is no contract field or validation step that could have caused this to revert. [5](#0-4)

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L24-37)
```text
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L205-243)
```java
    if (tokenQuant <= 0) {
      throw new ContractValidateException("withdraw token quant must greater than zero");
    }

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
