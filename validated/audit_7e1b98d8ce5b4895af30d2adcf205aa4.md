### Title
`ExchangeInjectContract` allows liquidity injection with no slippage/counterpart-amount protection - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java])

### Summary
`ExchangeInjectActuator` is java-tron's on-chain analog of an AMM `addLiquidity()` call: a user specifies one token (`token_id`) and an amount (`quant`) to inject into a TRX/TRC10 `Exchange` pool, and the actuator computes the required counterpart amount (`anotherTokenQuant`) from the *current* pool ratio, then deducts both amounts from the caller's balance. Unlike `ExchangeTransactionContract` (the trade/swap message), which carries an explicit `expected` field used as a slippage floor, `ExchangeInjectContract` has no analogous bound on the counterpart amount the caller is willing to pay.

### Finding Description
`ExchangeInjectContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no field to cap the counterpart token amount the injector is willing to spend. [1](#0-0) 

In `doValidate()`/`execute()`, the counterpart amount is derived purely from the pool's current balances at the moment the transaction is processed: [2](#0-1) 

That computed `anotherTokenQuant` is then unconditionally pulled from the user's account as long as the balance is sufficient — there is no check comparing it against any caller-supplied maximum: [3](#0-2) 

`execute()` recomputes the same ratio-based amount and deducts it from the account without re-validating against user intent: [4](#0-3) 

By contrast, `ExchangeTransactionActuator` (the swap path) explicitly protects the caller with a minimum-received check using the contract's `expected` field: [5](#0-4) 

This asymmetry is the direct analog of the reported `createPair()`/`addLiquidity()` issue: the swap-side operation has slippage protection, but the liquidity-injection-side operation does not. A pool's ratio can shift between the time a user signs an `ExchangeInjectContract` transaction and the time it is actually packed and executed in a block — e.g., another account executing an `ExchangeTransactionContract` (swap) or another `ExchangeInjectContract` against the same `exchange_id` beforehand (front-running/sandwiching), or simply normal trading activity while the transaction sits in the mempool. Because the injector's contract locks only `quant` of one token, the counterpart amount computed and charged at execution time can be arbitrarily different (worse) than what the user expected when they signed the transaction, and the transaction still succeeds as long as the account balance covers it (no revert, no protection).

### Impact Explanation
This is a concrete accounting/economic-loss impact for unprivileged users: any account owner (exchange creator, since `ExchangeInjectContract` requires the caller to be the exchange creator per `doValidate()` check at line 175) can be forced to inject an amount of the counterpart token far above what they intended if the pool ratio moves between signing and execution, due to front-running. There is no cap, and no failure mode protecting the user — the injection silently completes at whatever ratio exists at settlement time, directly transferring economic value away from the injector. [6](#0-5) 

### Likelihood Explanation
Moderate-to-high likelihood: any observer of pending transactions (mempool, or same-block ordering by SRs) can front-run an `ExchangeInjectContract` by first executing a trade (`ExchangeTransactionContract`) against the same `exchange_id` to shift the ratio, then let the victim's injection execute at the manipulated ratio. This requires no privileged access — only the ability to submit a transaction targeting the same exchange pool before the victim's transaction executes.

### Recommendation
Add a caller-supplied bound to `ExchangeInjectContract` (e.g., `another_token_quant_min` or `_max`, analogous to the `expected` field already used in `ExchangeTransactionContract`), and enforce it in both `ExchangeInjectActuator.doValidate()` and `execute()` before deducting the computed `anotherTokenQuant`, rejecting the transaction if the computed value falls outside the caller's tolerance.

### Proof of Concept
1. Exchange pool has balances `firstTokenBalance = A`, `secondTokenBalance = B`.
2. User A signs `ExchangeInjectContract{exchange_id, token_id=first, quant=q}` expecting counterpart amount `~ B*q/A`.
3. Before User A's transaction lands, an attacker submits `ExchangeTransactionContract` trading against the pool, shifting the ratio so `secondTokenBalance` becomes `B'` (much larger relative to `firstTokenBalance`).
4. User A's `ExchangeInjectContract` executes; `anotherTokenQuant` is recomputed in `execute()` using the new ratio, per lines 71-83, forcing User A to pay `B'*q/A' ≫` originally expected amount, with no revert or protection, as shown by the balance-sufficiency-only check at lines 238-256.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L209-227)
```java
    BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
    BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
    BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
    long newTokenBalance;
    long newAnotherTokenBalance;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L238-256)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
