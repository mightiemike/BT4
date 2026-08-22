This confirms the analog precisely. TRON's swap function `ExchangeTransactionActuator` (analogous to a DEX "swap") explicitly implements slippage protection via the `expected` field, checked at [1](#0-0) . However, the liquidity-provisioning actuators `ExchangeInjectActuator` (addLiquidity analog) and `ExchangeWithdrawActuator` (removeLiquidity analog) have no such protection field in their protobuf contracts [2](#0-1) , exactly matching the reported bug class.

### Title
Missing slippage/limit protection in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` allows unpredictable collateral spent or shares received - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java])

### Summary
TRON's on-chain bancor-style AMM (`Exchange`) exposes `ExchangeInjectContract` (add liquidity) and `ExchangeWithdrawContract` (remove liquidity) transactions. Unlike `ExchangeTransactionContract` (the swap/trade action), these two contracts do not carry any caller-specified bound on the paired-token amount that will be computed and moved during execution, so the amount of the "other side" token spent (inject) or received (withdraw) is fully determined by the pool's current ratio at execution time and can differ arbitrarily from what the submitter expected.

### Finding Description
`ExchangeInjectContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` [3](#0-2) . In `ExchangeInjectActuator.execute()`, the paired amount `anotherTokenQuant` is derived purely from the live pool balances at execution time (`floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance)`), and that amount is unconditionally deducted from the caller's account balance/assets [4](#0-3) . There is no validation step that lets the caller cap `anotherTokenQuant`; validate() recomputes the same value from the current state and only checks it is positive and under `balanceLimit` [5](#0-4) .

Symmetrically, `ExchangeWithdrawContract` has the same shape (`token_id`, `quant`, no limit field) [6](#0-5) , and `ExchangeWithdrawActuator` computes `anotherTokenQuant` (the amount of the other token credited back to the caller) from the pool's current ratio with no caller-supplied minimum-received bound, only a "not precise enough" rounding-tolerance check, not a value bound [7](#0-6) [8](#0-7) .

This is in direct contrast to the trade path: `ExchangeTransactionContract` includes an `expected` field [9](#0-8) , and `ExchangeTransactionActuator.doValidate()` explicitly rejects the transaction if the computed `anotherTokenQuant` is less than `tokenExpected` [1](#0-0) , i.e., the protocol designers already recognized and mitigated exactly this unpredictability problem for swaps but never applied the same protection to inject/withdraw.

Because the pool ratio (`firstTokenBalance`/`secondTokenBalance`) can move between when a user constructs/signs their transaction and when it is actually broadcast and executed on-chain — due to normal trading activity, other inject/withdraw operations, or transaction ordering/front-running by block producers — the actual paired-token amount spent (inject) or received (withdraw) is unpredictable and outside the caller's control at broadcast time.

### Impact Explanation
A user submitting `ExchangeInjectContract` can end up paying substantially more of the paired asset than intended (up to the `balanceLimit` cap), and a user submitting `ExchangeWithdrawContract` can receive substantially less paired-asset value than expected, both purely as a function of pool state drift/front-running between signing and execution. This is an accounting/economic-loss issue reachable by any account via a normal broadcast transaction, not requiring any privileged role — it only requires being the creator of that particular `Exchange` pair, which any account can become simply by issuing `ExchangeCreateContract`.

### Likelihood Explanation
Likelihood is moderate: it requires normal, expected pool activity (trades, other inject/withdraw operations) or intentional front-running between the time a user signs an inject/withdraw transaction and when it is included in a block. Since transaction contents (including `quant`) are visible in the mempool/broadcast before confirmation, an adversary (e.g., another user or a block producer) could deliberately trade against the pool immediately before the victim's inject/withdraw transaction executes to worsen the ratio the victim receives, similar to sandwich attacks in other AMMs.

### Recommendation
Add a bound field to `ExchangeInjectContract` (e.g., `max_another_quant` or similar) and to `ExchangeWithdrawContract` (e.g., `expected_another_quant` similar to `ExchangeTransactionContract.expected`), and enforce it in `ExchangeInjectActuator.doValidate()` / `ExchangeWithdrawActuator.doValidate()` the same way `ExchangeTransactionActuator` already enforces `tokenExpected` against the computed `anotherTokenQuant`.

### Proof of Concept
1. Account A creates an exchange pool with tokens X/Y at ratio 1:2 via `ExchangeCreateContract`.
2. Account A (the pool creator) signs `ExchangeInjectContract{token_id: X, quant: 100}` expecting to also pay ~200 Y based on the ratio observed at signing time, per the computation in `ExchangeInjectActuator.execute()` (`anotherTokenQuant = floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance)`) [10](#0-9) .
3. Before this transaction is packed into a block, other trades (`ExchangeTransactionContract`) against the same pool shift the ratio to 1:4.
4. When Account A's inject transaction executes, `anotherTokenQuant` is recomputed against the new ratio, so 400 Y is deducted from Account A instead of the ~200 Y they expected, up to the `getExchangeBalanceLimit()` cap; there is no field in `ExchangeInjectContract` that Account A could have set to cap this and cause the transaction to fail instead.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-29)
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
```

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L31-37)
```text
message ExchangeTransactionContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
  int64 expected = 5;
}
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-99)
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .reduceAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-236)
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

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
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
