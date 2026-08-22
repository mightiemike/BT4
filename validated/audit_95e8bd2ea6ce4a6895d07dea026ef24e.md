### Title
Unchecked return value of `AccountCapsule#addAssetAmountV2` in `TransferAssetActuator.execute` can silently lose transferred assets - (File: `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java`)

### Summary
`TransferAssetActuator.execute()` debits the asset balance from the sender via `reduceAssetAmountV2()` and explicitly checks its boolean result, throwing `ContractExeException` on failure. Immediately after, it credits the receiver via `addAssetAmountV2()` but discards the boolean return value entirely, exactly mirroring the reported bug class where a transfer/balance-mutation call's success is not verified before the flow proceeds to commit state and mark the transaction successful.

### Finding Description
In `execute()`:
```java
if (!ownerAccountCapsule
    .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
  throw new ContractExeException("reduceAssetAmount failed !");
}
accountStore.put(ownerAddress, ownerAccountCapsule);

toAccountCapsule
    .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
accountStore.put(toAddress, toAccountCapsule);
``` [1](#0-0) 

The `reduceAssetAmountV2` call's boolean result is checked and causes a revert (`ContractExeException`) if it fails, which is the correct pattern the external report recommends. However, `addAssetAmountV2` is called for its side effect only, and its boolean return value is dropped on the floor. If `addAssetAmountV2` can return `false` for any reason (e.g. an unexpected/malformed asset map state, an internal validation failure, or any code path that intentionally signals "not applied"), the actuator still proceeds to `accountStore.put(toAddress, toAccountCapsule)`, deducts the fee, and finally sets `ret.setStatus(fee, code.SUCESS)` — reporting the transaction as fully successful regardless of whether the credit side actually took effect.

This is the same root-cause shape as `RelayPolymarketSDK::transferUsdc()`: a balance-mutating operation exposes a success/failure signal, the caller ignores it, and the surrounding flow (fee accounting, success status, state commit) proceeds unconditionally.

### Impact Explanation
If the credit-side call can fail without throwing, the sender's asset balance is unconditionally reduced (and the check for that reduction succeeding is enforced), but the corresponding credit to the receiver may not be applied, while the transaction is still recorded as `SUCESS`. That destroys the asset's total supply/accounting invariant (tokens debited from one account never appear in another), corrupting on-chain asset accounting — a High impact class per the report's own categorization (loss of funds / asset accounting corruption).

### Likelihood Explanation
Likelihood is Low, matching the original report's rating: under normal circumstances the reduce/add pair operates on already-validated inputs (amount > 0, `validate()` pre-checks existence and overflow), so `addAssetAmountV2` is expected to succeed whenever `reduceAssetAmountV2` did. The asymmetric handling — checking one side but not the other — is nonetheless a genuine code defect reachable from any anonymous `TransferAssetContract` broadcast transaction, and a latent code path (future refactor, edge case in asset map state) that makes `addAssetAmountV2` return `false` would silently corrupt balances with no error surfaced.

### Recommendation
Check the boolean return value of `addAssetAmountV2` the same way `reduceAssetAmountV2` is checked, and throw `ContractExeException` (reverting the transaction, including the already-applied `reduceAssetAmountV2` mutation) if it returns `false`, so that a failed credit can never be masked by a `SUCESS` status.

### Proof of Concept
Not applicable as a live exploit — this is a code-review-level defect. Reasoning: In `execute()`, `reduceAssetAmountV2` is guarded by `if (!... ) throw ...` while the very next call, `addAssetAmountV2`, is invoked without inspecting its return value before `accountStore.put()` and `ret.setStatus(fee, code.SUCESS)` are executed. [2](#0-1)  This is the closest concrete, reachable-from-broadcast-transaction analog of the reported "unverified transfer result" bug class within the actuator/state-transition code; I was unable to fully inspect the internal implementation of `AccountCapsule.addAssetAmountV2` (only its call sites were indexed) to confirm a concrete condition under which it returns `false`, so the exact triggering condition for the silent failure remains unverified and should be checked directly in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L75-92)
```java
      AccountCapsule ownerAccountCapsule = accountStore.get(ownerAddress);
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
      accountStore.put(ownerAddress, ownerAccountCapsule);

      toAccountCapsule
          .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
      accountStore.put(toAddress, toAccountCapsule);

      adjustBalance(accountStore, ownerAccountCapsule, -fee);
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      ret.setStatus(fee, code.SUCESS);
```
