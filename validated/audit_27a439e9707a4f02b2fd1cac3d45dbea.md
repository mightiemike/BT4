This confirms the analog. `ExchangeInjectContract` and `ExchangeWithdrawContract` (used for injecting/withdrawing liquidity into TRON's on-chain Bancor-style token exchange pools) carry no user-supplied minimum/maximum bound on the second token amount, unlike `ExchangeTransactionContract` which has an `expected` field enforced in `ExchangeTransactionActuator`. [1](#0-0) 

### Title
No slippage protection when injecting liquidity into TRON's on-chain Exchange (Bancor pool) - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java)

### Summary
`ExchangeInjectActuator` computes the paired-token amount (`anotherTokenQuant`) required to inject liquidity purely from the exchange pool's balances at the time the transaction is executed on-chain, and the `ExchangeInjectContract` protobuf message provides no field for the caller to bound that amount. [2](#0-1)  This is the same missing-slippage-bound bug class as the reported Uniswap `addLiquidity` finding, where `amountAMin`/`amountBMin` are hardcoded to 0.

### Finding Description
`ExchangeInjectContract` only contains `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no minimum/maximum bound field for the calculated counterpart amount. [3](#0-2) 

In `ExchangeInjectActuator.execute`, `anotherTokenQuant` is derived from the current on-chain pool ratio (`firstTokenBalance`/`secondTokenBalance`) at block-execution time and then unconditionally deducted from the caller's account balance/asset via `reduceAssetAmountV2`, with no comparison against any value the caller supplied: [4](#0-3) 

`doValidate` recomputes the same ratio using `BigInteger` math and only checks that `anotherTokenQuant > 0` and that the resulting balances don't exceed `getExchangeBalanceLimit()` — again, no user-defined slippage bound is enforced: [5](#0-4) 

By contrast, `ExchangeTransactionContract`/`ExchangeTransactionActuator` (the swap actuator) does carry and enforce a caller-supplied `expected` minimum-output field, rejecting the transaction if the swap output falls below it: [6](#0-5)  This shows the protocol designers recognized the need for slippage protection for swaps but omitted the equivalent protection for liquidity injection (and withdrawal — `ExchangeWithdrawContract` similarly has no user-supplied bound field, only an internal `allowHarden` precision-drift check unrelated to price movement [7](#0-6) ).

### Impact Explanation
Because pool balances (`firstTokenBalance`/`secondTokenBalance`) can be altered by any other `ExchangeTransactionContract` (swap) broadcast and confirmed between the time a user signs/broadcasts an `ExchangeInjectContract` transaction and the time it is actually included in a block, the actual `anotherTokenQuant` debited from the injecting account can differ arbitrarily from what the user expected when signing the transaction. An attacker (or normal market activity) can shift the pool ratio via a swap transaction ordered immediately before the victim's pending inject transaction, causing the victim to deposit a far larger amount of the second asset (TRX or a TRC10 token) than intended for the same `quant` of the first asset — a direct, unbounded economic loss to the liquidity provider, since there is no cap the user can enforce on-chain.

### Likelihood Explanation
Any account holding tokens in an exchange pair pool balance and TRX for fees can trigger this by submitting `ExchangeInjectContract`; the actuator is reachable directly from a signed, broadcast transaction with no privileged role required. Exploitation requires only the ability to front-run/interleave a swap transaction against the same exchange ID before the victim's inject transaction is processed, which is a normal capability of any network participant submitting transactions to the mempool.

### Recommendation
Add a caller-supplied bound field (e.g., `expected_another_token_amount` or `min_another_token_amount` / `max_another_token_amount`) to `ExchangeInjectContract` (and analogously to `ExchangeWithdrawContract`), and enforce it in `ExchangeInjectActuator.doValidate`/`execute` the same way `ExchangeTransactionActuator` enforces `tokenExpected` against `anotherTokenQuant`, rejecting the transaction with a `ContractValidateException` if the computed `anotherTokenQuant` falls outside the caller's tolerance.

### Proof of Concept
1. Exchange pool P has `firstTokenBalance = A`, `secondTokenBalance = B` (ratio A:B).
2. Victim broadcasts `ExchangeInjectContract{exchange_id=P, token_id=firstToken, quant=Q}`, expecting to inject roughly `Q*B/A` of the second token based on the ratio observed at broadcast time (computed in `ExchangeInjectActuator.execute`, lines 71-76 [8](#0-7) ).
3. Attacker submits an `ExchangeTransactionContract` swap against pool P that shifts the ratio significantly, and ensures it is included in the block before the victim's inject transaction (e.g., via fee/ordering manipulation or simply due to normal mempool timing).
4. When the victim's inject transaction executes, `anotherTokenQuant` is recomputed against the new, shifted ratio, and `reduceAssetAmountV2` deducts this larger-than-expected amount from the victim's account with no minimum/maximum guard, resulting in the victim losing more of the second asset than intended for the same `quant` of the first asset.

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L1-1)
```text
syntax = "proto3";
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-246)
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

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L190-221)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-243)
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
```
