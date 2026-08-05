This confirms a strong analog. The `ExchangeTransactionContract` explicitly includes an `expected` field checked in `ExchangeTransactionActuator.doValidate()` at [1](#0-0) , proving java-tron's designers recognized the need for slippage protection on AMM-style trades. However, `ExchangeWithdrawContract`/`ExchangeWithdrawActuator` (LP-style withdrawal from the bancor-style TRC10 exchange pool) has no such `expected`/minimum field — the `anotherTokenQuant` (the amount of the second token returned to the withdrawing pool creator) is entirely computed from the exchange's current reserve ratio at execution time, with no way for the caller to bound it.This confirms the schema: `ExchangeInjectContract` and `ExchangeWithdrawContract` both only carry `token_id` and `quant` — no `expected`/min-out field — while `ExchangeTransactionContract` explicitly has an `expected` field [2](#0-1) . This is enough to finalize the analysis: `ExchangeWithdrawActuator` (LP-equivalent withdrawal from java-tron's bancor-style TRC10 exchange pool) has no slippage protection, structurally identical to the reported Velar Artha burn bug.

### Title
LPs withdrawing from TRC10 Exchange pools cannot set a minimum amount for the other-side token, causing loss of funds - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
java-tron's built-in bancor-style TRC10 `Exchange` (pools created via `ExchangeCreateContract`, traded via `ExchangeTransactionContract`, and managed by the pool creator via `ExchangeInjectContract`/`ExchangeWithdrawContract`) lets the pool creator ("LP") withdraw liquidity through `ExchangeWithdrawContract`. Unlike `ExchangeTransactionContract`, which includes an `expected` field enforced in validation to protect traders from price movement, `ExchangeWithdrawContract` has no equivalent minimum-amount field for the "other side" token the LP receives back. This is the same root cause as the reported Sherlock finding: the amount returned on withdrawal is computed purely from the pool's current reserve ratio at execution time, and the withdrawer has no way to bound how much they will actually receive.

### Finding Description
`ExchangeWithdrawContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` [3](#0-2) . When `ExchangeWithdrawActuator.execute()` runs, `anotherTokenQuant` (the amount of the paired token returned to the LP) is derived from the *current* `firstTokenBalance`/`secondTokenBalance` ratio at execution time via a simple proportional formula: [4](#0-3) .

The `doValidate()` method recomputes the same ratio and only checks it is "precise enough" relative to the recomputation itself — a self-consistency check, not a user-supplied minimum-received guard: [5](#0-4) .

Between the time the LP signs and broadcasts an `ExchangeWithdrawContract` transaction and the time it is actually packed into a block and executed, other transactions touching the same exchange pool (in particular `ExchangeTransactionContract` trades processed earlier in the same block, or other injects/withdrawals) can shift `firstTokenBalance`/`secondTokenBalance`. Because the withdraw actuator has no `expected`/minimum parameter, the LP has no on-chain mechanism to reject execution if the pool ratio has moved unfavorably — directly analogous to the Velar Artha `burn()` bug where LPs could not bound `min_base`/`min_quote` received.

By contrast, java-tron's designers clearly recognized this exact risk for `ExchangeTransactionContract` (a simple swap) and added `expected`, enforced by `if (anotherTokenQuant < tokenExpected) { throw ... "token required must greater than expected"; }` [6](#0-5) . The same protection was never applied to `ExchangeWithdrawContract` (or `ExchangeInjectContract`), leaving a gap for LP-side operations that, per the Sherlock precedent, is a real and recognized loss-of-funds vector.

### Impact Explanation
An LP withdrawing from a TRC10 `Exchange` pool can receive materially less of the paired token than they calculated off-chain when submitting the transaction, if the pool's reserve ratio shifts (e.g., due to profitable trades against the pool) between transaction construction and block inclusion. This is a direct, quantifiable loss of funds for the withdrawer, with no way to cap the loss on-chain — matching the accepted Sherlock classification of Medium severity ("slippage related issue showing a definite loss of funds").

### Likelihood Explanation
The `Exchange` feature is a core, unprivileged, user-facing actuator (`ExchangeWithdrawActuator`) reachable by any account that created an exchange pool. Adverse price movement between signing and inclusion is a normal, expected condition on any live chain with concurrent transaction traffic — no special conditions are required beyond ordinary transaction ordering within blocks, which the LP cannot control.

### Recommendation
Add an `expected` (minimum-received) field to `ExchangeWithdrawContract`, mirroring `ExchangeTransactionContract`, and enforce it in `ExchangeWithdrawActuator.doValidate()`/`execute()` by rejecting the transaction if the computed `anotherTokenQuant` falls below the caller-specified minimum. Consider the same fix for `ExchangeInjectContract` if injectors have analogous exposure to unfavorable exchange rates at execution time.

### Proof of Concept
1. Pool creator A creates an `Exchange` pool via `ExchangeCreateContract` with tokens `X`/`Y` at a 1:1 ratio.
2. A signs an `ExchangeWithdrawContract` for `quant` of `X`, expecting to receive `anotherTokenQuant` of `Y` computed off the current 1:1 ratio (per `ExchangeWithdrawActuator.execute()` logic at [7](#0-6) ).
3. Before A's transaction is packed, another user submits `ExchangeTransactionContract` trades against the same pool, shifting the `firstTokenBalance`/`secondTokenBalance` ratio significantly in favor of `Y` being scarcer.
4. A's `ExchangeWithdrawContract` is then included; `anotherTokenQuant` is recomputed from the now-skewed ratio, yielding far less `Y` than A anticipated — with no `expected` field to abort the transaction, unlike what would happen with `ExchangeTransactionContract`'s `tokenExpected` check at [8](#0-7) .
5. A's withdrawal executes and succeeds despite the unfavorable rate, resulting in a quantifiable loss.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L175-221)
```java
    long tokenExpected = contract.getExpected();

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

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
