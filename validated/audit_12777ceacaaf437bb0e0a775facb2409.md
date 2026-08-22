Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` have no `expected`/min-out field, unlike `ExchangeTransactionContract` which explicitly has `expected` (int64 expected = 5) used as slippage protection. This is the exact analog to the Particle bug.

### Title
Missing slippage/output-bound protection in ExchangeInjectActuator and ExchangeWithdrawActuator allows front-running of pool-ratio-dependent amounts - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
TRON's Bancor-style `Exchange`/`ExchangeV2` AMM pools support `ExchangeInjectContract` (add liquidity) and `ExchangeWithdrawContract` (remove liquidity). Both compute the counterpart token amount (`anotherTokenQuant`) at execution time from the *current* pool ratio, but their protobuf messages carry no user-controlled minimum/maximum bound field, unlike `ExchangeTransactionContract` which has an explicit `expected` field used precisely for this purpose.

### Finding Description
In `ExchangeInjectActuator.doValidate()`/`execute()`, `anotherTokenQuant` is derived purely from `firstTokenBalance`/`secondTokenBalance` at block-execution time: [1](#0-0) . The only bound check is `anotherTokenQuant <= 0` [2](#0-1) , with no upper bound the creator can set.

Compare this to `ExchangeTransactionContract`/`ExchangeTransactionActuator`, which explicitly validates `anotherTokenQuant < tokenExpected` as slippage protection: [3](#0-2) . The `ExchangeTransactionContract` proto carries this dedicated `expected` field [4](#0-3) , while `ExchangeInjectContract` and `ExchangeWithdrawContract` do not [5](#0-4) .

`ExchangeWithdrawActuator` has the identical pattern: `anotherTokenQuant` is computed from the live pool balances with no user-supplied minimum, only a `<= 0` sanity check and a "precision" tolerance check unrelated to price movement: [6](#0-5) .

Any anonymous account can broadcast an `ExchangeTransactionContract` (a normal swap against the same pool, callable by anyone, not privileged) to shift `firstTokenBalance`/`secondTokenBalance` before the block containing a pending Inject/Withdraw transaction is packed. Since Inject/Withdraw execution recomputes `anotherTokenQuant` from the ratio at execution time and there is no `expected`-style bound in the request, the actual counterpart amount consumed/returned can differ arbitrarily from what the signer intended when they built and broadcast the transaction — this mirrors exactly the Particle `AddLiquidity`/`decreaseLiquidity` missing `amount0Min`/`amount1Min` root cause.

### Impact Explanation
- For Inject: a frontrunning swap can shift the ratio so that the creator's fixed `tokenQuant` now requires depositing a much larger `anotherTokenQuant` than intended, forcing an unintentional, unfavorable liquidity contribution (or causing the tx to fail if balance is insufficient, a griefing/DoS on the creator's intended operation).
- For Withdraw: a frontrunning swap can shift the ratio so the creator receives significantly less `anotherTokenQuant` back than expected when withdrawing a fixed `tokenQuant` of liquidity, resulting in a direct value loss extractable via a sandwich attack (buy before Withdraw executes, sell after).
- Both operations are only callable by the exchange creator (`accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())` [7](#0-6) ), but the attacker triggering the ratio shift (via `ExchangeTransactionContract`) is unprivileged and can act on any broadcast transaction visible in the mempool.

### Likelihood Explanation
Moderate-to-high: anyone monitoring the mempool for pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transactions can trivially construct a sandwiching `ExchangeTransactionContract` swap since exchange trading is permissionless and the pool state/ratio is fully public via `ExchangeCapsule`/`ExchangeStore`.

### Recommendation
Add an explicit slippage-bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to `expected` in `ExchangeTransactionContract`), e.g. `expected_another_token_quant` for Inject (minimum acceptable counterpart deposit... actually a maximum, since depositor wants to cap cost) and a minimum acceptable `anotherTokenQuant` for Withdraw, and enforce these bounds in `ExchangeInjectActuator.doValidate()` / `ExchangeWithdrawActuator.doValidate()` before execution, mirroring the `tokenExpected` check already present in `ExchangeTransactionActuator`.

### Proof of Concept
1. Exchange creator holds a pool with `firstTokenBalance=1000`, `secondTokenBalance=1000`.
2. Creator builds and broadcasts `ExchangeInjectContract{token_id=first, quant=100}`, expecting `anotherTokenQuant≈100` based on the ratio at signing time.
3. Before this tx is packed, an attacker broadcasts `ExchangeTransactionContract` swapping a large amount of `secondToken` for `firstToken`, drastically changing the ratio (e.g., to `firstTokenBalance=1100`, `secondTokenBalance=500`).
4. The creator's Inject tx executes using the new ratio: `anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance` computed at execution time [8](#0-7) , forcing the creator to deposit a different `secondToken` amount than intended, with no on-chain bound to reject the unfavorable execution.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L73-76)
```java
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L229-231)
```java
    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-254)
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
