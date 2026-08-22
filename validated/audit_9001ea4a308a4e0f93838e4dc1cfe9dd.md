### Title
Missing Slippage Protection in `ExchangeInjectContract`/`ExchangeWithdrawContract` (Unlike `ExchangeTransactionContract`) - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Java-tron's built-in Bancor-style AMM (`ExchangeCapsule`) exposes three user-facing operations that convert one token amount into another based on the current pool ratio: inject (add liquidity), withdraw (remove liquidity), and transaction (swap). Of these, only `ExchangeTransactionContract` lets the caller specify a slippage bound (`expected`), which is enforced in `ExchangeTransactionActuator.doValidate()`. `ExchangeInjectContract` and `ExchangeWithdrawContract` provide no equivalent bound: the counter-token amount (`anotherTokenQuant`) is computed at execution time strictly from the live pool ratio, with no way for the submitter to cap or floor it, mirroring exactly the missing-slippage-protection pattern described in the external report for `FundContract.deposit()`/`withdraw()`.

### Finding Description
`ExchangeTransactionContract` carries an `expected` field [1](#0-0)  that is validated against the computed output before the trade executes [2](#0-1) .

By contrast, `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — no minimum/maximum bound field [3](#0-2) .

In `ExchangeInjectActuator`, `anotherTokenQuant` (the amount of the paired token that will be pulled from the injecting account to preserve the pool ratio) is computed purely from the exchange's current `firstTokenBalance`/`secondTokenBalance` at validation/execution time, with only a solvency check (`assetBalanceEnoughV2`) — no check that this amount matches what the submitter expected when they built the transaction: [4](#0-3) .

In `ExchangeWithdrawActuator`, `anotherTokenQuant` (the amount of the paired token credited back to the withdrawer) is likewise computed purely from the live pool ratio, with only a "not precise enough" rounding check and a pool-solvency check — never a user-supplied minimum acceptable amount: [5](#0-4) .

Because these ratios are the same `firstTokenBalance`/`secondTokenBalance` fields mutated by every `ExchangeTransactionContract` trade against that exchange pair, any pending inject/withdraw transaction is exposed to ratio drift caused by other transactions ordered ahead of it in the same block or mempool — the identical time-of-check/time-of-execution exposure that the external report flags for ERC-4626 `deposit`/`withdraw` without a `minSharesOut`/`minAssetsOut` guard.

### Impact Explanation
An exchange creator submitting `ExchangeInjectContract` can end up paying a materially different (larger) amount of the paired token than intended if the pool ratio shifts favorably-for-attacker/unfavorably-for-victim before inclusion, since the debited `anotherTokenQuant` is derived from the ratio at execution time, not submission time. Symmetrically, an `ExchangeWithdrawContract` submitter can receive fewer paired tokens than the ratio implied when they signed the transaction. Because TRX and TRC10 balances move directly (no intermediate share token with its own redemption logic), any adverse execution directly and irreversibly changes account balances — a medium-severity value-extraction risk consistent with the referenced report's classification, though bounded to loss on the injecting/withdrawing account rather than protocol-wide insolvency (the pool-balance invariant checks prevent negative balances).

### Likelihood Explanation
Exploitation requires an attacker (or unrelated normal trading activity) to submit `ExchangeTransactionContract` trades against the same `exchange_id` timed to land before a pending inject/withdraw transaction, shifting `firstTokenBalance`/`secondTokenBalance`. This is straightforward for any address given `ExchangeTransactionContract` is broadcastable by any account with sufficient balance, and transaction ordering within a block/mempool is not guaranteed to match submission order. The reachable surface is the standard node transaction broadcast path (`wallet/broadcasttransaction`), requiring no privileged role — only that the victim be the specific exchange's creator (the only party authorized to call inject/withdraw for that exchange), which does not constitute a privileged node/consensus role.

### Recommendation
Add a slippage-bound field (e.g., `expected`) to `ExchangeInjectContract` and `ExchangeWithdrawContract`, mirroring `ExchangeTransactionContract.expected`, and enforce it in `ExchangeInjectActuator.doValidate()`/`ExchangeWithdrawActuator.doValidate()` by rejecting the transaction if the computed `anotherTokenQuant` falls below the caller-specified minimum (for withdraw) or exceeds the caller-specified maximum (for inject), analogous to the existing check at `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java:219-221`.

### Proof of Concept
1. Attacker/creator sets up an exchange pair (TRX/TokenA) with balances B1, B2.
2. Creator broadcasts `ExchangeInjectContract` (or `ExchangeWithdrawContract`) expecting `anotherTokenQuant ≈ f(B1, B2, quant)` based on the ratio observed when building the transaction.
3. Before that transaction is packed into a block, any third party broadcasts one or more `ExchangeTransactionContract` swaps against the same `exchange_id`, shifting `B1`/`B2` (this is permitted with no ordering guarantee relative to the pending inject/withdraw tx).
4. The creator's inject/withdraw transaction executes against the now-shifted ratio in `ExchangeInjectActuator.execute()`/`ExchangeWithdrawActuator.execute()`, producing an `anotherTokenQuant` materially different from what was expected, debiting/crediting the creator's account accordingly — with no on-chain mechanism (`expected`-style field) available to have reverted the transaction instead.

### Citations

**File:** Tron protobuf protocol document.md (L1384-1420)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-256)
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

    if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(anotherTokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(anotherTokenID, anotherTokenQuant, dynamicStore)) {
        throw new ContractValidateException("another token balance is not enough");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-272)
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

      if (allowHarden) {
        BigDecimal remainder = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
      }
    }
```
