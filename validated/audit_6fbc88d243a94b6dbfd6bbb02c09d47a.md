## Analysis: Ignored Return Value in `ExchangeTransactionActuator.execute`

### Finding Description

The external report flags `ClaimAssessor.requestClaim` for silently ignoring a boolean success/failure return value from a sub-call, which can let processing continue on failure. The equivalent bug class exists in java-tron's `ExchangeTransactionActuator.execute`.

`AccountCapsule` exposes `reduceAssetAmountV2` and `addAssetAmountV2` as boolean-returning methods that indicate whether the asset balance adjustment actually succeeded (mirroring the same accounting-mutation pattern used elsewhere in the codebase). `TransferAssetActuator.execute` explicitly checks the return of `reduceAssetAmountV2` and throws a `ContractExeException` if it fails: [1](#0-0) 

However, `ExchangeTransactionActuator.execute` calls both `reduceAssetAmountV2` and `addAssetAmountV2` and discards their boolean return values entirely: [2](#0-1) 

After these unchecked calls, the actuator unconditionally commits the mutated account (`accountStore.put`), persists the updated exchange pool state (`Commons.putExchangeCapsule`), and marks the transaction as `SUCESS`: [3](#0-2) 

If `reduceAssetAmountV2`/`addAssetAmountV2` return `false` (e.g., due to the asset key not being found in the account's asset map, or an internal overflow/edge case handled internally by returning false rather than throwing), the actuator has no way to detect this and will still commit the account and exchange state as if the debit/credit succeeded.

### Impact Explanation

Because the `exchangeCapsule.transaction(...)` pool-balance update (line 68) already happened and is committed via `Commons.putExchangeCapsule` regardless of whether the token amount was actually deducted from/credited to the user's `AccountCapsule`, a failure in `reduceAssetAmountV2`/`addAssetAmountV2` would desynchronize the exchange pool's book-kept balances from the actual sum of user asset balances — a state-accounting divergence reachable by any unprivileged user submitting an `ExchangeTransactionContract`.

### Likelihood Explanation

This is lower-severity than a directly exploitable double-spend because `validate()` calls `assetBalanceEnoughV2` beforehand to ensure sufficient balance for the reduce path, which reduces (but does not eliminate, since `reduceAssetAmountV2`/`addAssetAmountV2` can fail for reasons other than insufficient balance, e.g., missing asset map entry) the chance of a `false` return in production. I was not able to fully inspect the internal implementation of `AccountCapsule.addAssetAmountV2`/`reduceAssetAmountV2` to enumerate all failure branches within this session, so I cannot conclusively prove a reachable false-return path is exploitable versus merely defensive/theoretical.

### Recommendation
Check the boolean return values of `reduceAssetAmountV2` and `addAssetAmountV2` in `ExchangeTransactionActuator.execute` (and similarly in other actuators using these methods for a credit path) and throw `ContractExeException` on failure, consistent with the pattern already used in `TransferAssetActuator.execute`.

### Proof of Concept
Not independently confirmed — would require exercising `AccountCapsule.addAssetAmountV2`/`reduceAssetAmountV2` internals to force a `false` return (e.g., stale/missing asset map entry) and observe that `ExchangeTransactionActuator` still commits `code.SUCESS` and updates the exchange pool. This could not be fully verified within the scope of this session; a Devin session with full file access and test execution would be needed to confirm the exact failure conditions of `AccountCapsule.addAssetAmountV2`/`reduceAssetAmountV2`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L76-79)
```java
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L80-91)
```java
      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L93-99)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```
