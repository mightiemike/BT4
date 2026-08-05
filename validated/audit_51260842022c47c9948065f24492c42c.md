## Title
Missing slippage protection in `ExchangeWithdrawContract` liquidity withdrawal enables value loss via pool-ratio manipulation - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeWithdrawActuator` computes the counter-token amount a liquidity provider receives strictly from the exchange pool's balances *at execution time*, with no user-supplied minimum-output parameter to bound the payout. This mirrors the reported class of bug (an unprotected swap/liquidity conversion executed with `amountOutMinimum = 0`), which allows the actual settlement amount to diverge arbitrarily from what the user expected when they submitted the transaction, because the pool ratio can be moved between submission and execution.

### Finding Description
`ExchangeWithdrawContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no analog of the `expected` field that `ExchangeTransactionContract` deliberately includes for slippage protection: [1](#0-0) 

In `ExchangeWithdrawActuator.execute`, `anotherTokenQuant` (the amount of the counter-asset paid out to the withdrawer) is derived purely from the current on-chain pool balances (`firstTokenBalance`/`secondTokenBalance`) and the requested `tokenQuant`, using the AMM-style ratio formula, with no lower bound supplied by the caller: [2](#0-1) 

`doValidate` confirms there is no `expected`/minimum-output check anywhere in the validation path — it only checks that pool balances are sufficient and that `anotherTokenQuant` is nonzero and "precise enough", never that it meets a caller-specified floor: [3](#0-2) 

By contrast, the sibling actuator `ExchangeTransactionActuator` explicitly validates that the computed output meets the caller's `tokenExpected` before allowing execution — proving the codebase is aware of this class of risk and mitigates it in the trade path but not the withdraw path: [4](#0-3) 

Because pool balances can be moved by any unprivileged user submitting `ExchangeTransactionContract`, `ExchangeInjectContract`, or another `ExchangeWithdrawContract` against the same `exchange_id` before the withdrawer's transaction is packed into a block, an attacker (or even normal market activity) can shift the ratio and cause the withdrawer to receive substantially less of the counter-asset than anticipated when they built and signed their transaction — the exact "unprotected swap conversion, no minimum output enforced" pattern from the report, just applied to liquidity withdrawal instead of a debt-repayment swap.

### Impact Explanation
Impact is medium: a liquidity provider withdrawing from an exchange pool can receive fewer counter-tokens than the pool ratio implied when they submitted the transaction, resulting in direct value loss during settlement. This is an accounting/settlement divergence between user intent and on-chain execution outcome, analogous to the margin-account underpayment described in the source report.

### Likelihood Explanation
Likelihood is medium: it requires an attacker (or coincidental market activity) to alter the pool's token ratio between the time the withdraw transaction is broadcast and the time it is included in a block — feasible given TRON's public mempool visibility and short block times, similar to standard sandwich/front-running conditions on AMM-style pools.

### Recommendation
Add an `expected`/minimum-output field to `ExchangeWithdrawContract`, and enforce it in `ExchangeWithdrawActuator.doValidate`/`execute` (mirroring the check already present in `ExchangeTransactionActuator`), rejecting the withdrawal if the computed `anotherTokenQuant` falls below the caller-specified minimum.

### Proof of Concept
1. User A creates an exchange pool via `ExchangeCreateContract` and becomes its creator.
2. User A observes pool balances and builds an `ExchangeWithdrawContract` transaction expecting to receive `X` units of the counter-token for `tokenQuant` of the base token, based on the current ratio.
3. Before User A's transaction is included, an attacker submits an `ExchangeTransactionContract` (or another actor injects/withdraws liquidity) against the same `exchange_id`, shifting `firstTokenBalance`/`secondTokenBalance`.
4. User A's withdrawal executes against the new, manipulated ratio in `ExchangeWithdrawActuator.execute`, yielding `anotherTokenQuant` significantly lower than `X`, with no validation check able to reject it since no minimum-output parameter exists — see `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java:68-89` and the absence of an `expected` check contrasted with `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java:217-221`.

### Citations

**File:** Tron protobuf protocol document.md (L1403-1442)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L68-89)
```java
      byte[] tokenID = exchangeWithdrawContract.getTokenId().toByteArray();
      long tokenQuant = exchangeWithdrawContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-243)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
