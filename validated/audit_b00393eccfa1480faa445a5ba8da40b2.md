### Title
Unchecked return value of `addAssetAmountV2()` in `ExchangeWithdrawActuator.execute()` can silently fail to credit a withdrawn token, causing accounting divergence (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeWithdrawActuator.execute()` calls the boolean-returning method `AccountCapsule.addAssetAmountV2()` twice to credit the withdrawn token amounts to the account, but discards the return value in both calls, while the exchange-side balances are unconditionally decremented beforehand. This mirrors the reported bug class of ignoring a token-transfer/accounting call's success/failure signal (`transfer()`/`transferFrom()` return value not checked), leading to silent loss of accounted value.

### Finding Description
In `ExchangeWithdrawActuator.execute()`, the exchange pool's balances are reduced via `exchangeCapsule.setBalance(...)` unconditionally [1](#0-0) . Immediately after, the withdrawn token amounts are supposed to be credited back to the user's account via `accountCapsule.addAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore)` and `accountCapsule.addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore)`, but neither call's boolean return value is checked or acted upon [2](#0-1) . `AccountCapsule` exposes `addAssetAmountV2`/`reduceAssetAmountV2` as `boolean`-returning methods designed to signal success/failure of the balance mutation, as confirmed by the method signatures in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java`. Elsewhere in the codebase this contract is honored correctly: `TransferAssetActuator.execute()` explicitly checks the return value of `reduceAssetAmountV2()` and throws a `ContractExeException` on failure [3](#0-2) . `ExchangeWithdrawActuator` breaks this pattern, ignoring the same class of call whose failure would otherwise indicate that the accounting mutation did not take effect.

### Impact Explanation
If `addAssetAmountV2()` returns `false` (analogous to a silently-failing `transfer()` returning `false` instead of reverting), the exchange pool has already had its liquidity permanently reduced (`exchangeCapsule.setBalance(...)`), but the user's account is never credited with the withdrawn token(s). This produces a state-accounting divergence: tokens vanish from the exchange pool without being minted/credited anywhere, permanently corrupting total-supply-style invariants tracked via `AssetIssueStore`/`AccountStore` balances. This falls under invalid-state/accounting-divergence impact.

### Likelihood Explanation
The `ExchangeWithdrawContract` is reachable by any account address that is the creator of an exchange (validated in `doValidate()` by ownership check only) [4](#0-3) , i.e., an unprivileged user-facing operation, not a trusted-role-only path. Whether `addAssetAmountV2` can actually return `false` in this call path (e.g., due to overflow guards or asset-not-found conditions inside `AccountCapsule`) was not fully verifiable from the available snippets, so the exact triggering precondition for the return-value failure is unconfirmed from the code excerpts I could inspect.

### Recommendation
Check the boolean return values of both `addAssetAmountV2()` calls in `ExchangeWithdrawActuator.execute()` and throw a `ContractExeException` (mirroring `TransferAssetActuator`'s pattern) on failure, rather than silently continuing to commit the exchange-pool balance reduction.

### Proof of Concept [5](#0-4) 

Compare with the correctly-checked pattern in the sibling actuator: [3](#0-2)

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L91-106)
```java
      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, tokenQuant));
      } else {
        accountCapsule.addAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-182)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L75-79)
```java
      AccountCapsule ownerAccountCapsule = accountStore.get(ownerAddress);
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
```
