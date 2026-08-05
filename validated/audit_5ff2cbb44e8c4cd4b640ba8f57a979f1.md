### Title
Failed/reverted contract-creation transactions skip `resetAccountUsage` while still merging and billing frozen-energy usage, permanently inflating the creator's energy window - ([File: chainbase/src/main/java/org/tron/core/db/TransactionTrace.java])

### Summary
`TransactionTrace.pay()` only calls `resetAccountUsage()` for the caller/creator account when `dynamicPropertiesStore.supportUnfreezeDelay()` is true **and** `getRuntimeResult().getException() == null && !getRuntimeResult().isRevert()`. For `TRX_CONTRACT_CREATION_TYPE`, an attacker can trivially force a deterministic revert/exception in the constructor, so this guard is false while the energy-merge pre-write done earlier in `VMActuator` and the unconditional `receipt.payEnergyBill(...)` call still execute, leaving the account's frozen-energy window/usage bookkeeping unreconciled.

### Finding Description
In `pay()`, for `TRX_CONTRACT_CREATION_TYPE`, `callerAccount == originAccount` (both are `trx.getOwnerAddress()`), so only the caller branch of `resetAccountUsage` is relevant: [1](#0-0) [2](#0-1) 

The gate that decides whether `resetAccountUsage` runs depends solely on `getException() == null && !isRevert()`, both of which are fully attacker-controlled by embedding a `REVERT` opcode (or any deterministic failure) in the constructor bytecode of a `CreateSmartContract` transaction.

Before execution even starts, during `validate()`, `VMActuator.create()` calls `getAccountEnergyLimitWithFixRatio(creator, feeLimit, callValue)` (when `StorageUtils.getEnergyLimitHardFork()` is enabled), and — when `VMConfig.allowTvmFreezeV2()` is on — this function performs a "pre-merge" of the account's frozen-energy usage/window *before* it is known whether the transaction will revert: [3](#0-2) 

This writes the merged `account.setEnergyUsage(...)` value and records the pre-/post-merge state into the receipt (`setCallerEnergyUsage/WindowSize` and `setCallerEnergyMergedUsage/WindowSize`) — exactly the fields `resetAccountUsage` needs to invert the merge: [4](#0-3) 

Because this pre-merge write happens in `validate()`, which always runs regardless of the later `execute()` outcome, and because `pay()` skips the corresponding `resetAccountUsage` call whenever the transaction reverts/throws, the merge is never unwound for failed creations. `receipt.payEnergyBill(...)` is called unconditionally right after the guarded block, so `EnergyProcessor.useEnergy` also applies its own merge/window computation on top of the already-merged (unreset) state: [5](#0-4) 

This produces the exact asymmetry the question describes: for successful creations the merge is undone and replaced by the true billed usage; for reverted/exceptional creations, the reservation-time merge stands permanently and is compounded by `payEnergyBill`'s own merge, inflating the account's `EnergyUsage`/`WindowSize` fields beyond what actually-billed usage should represent.

### Impact Explanation
An attacker who repeatedly submits deterministically-reverting `CreateSmartContract` transactions from the same account can inflate that account's on-chain frozen-energy usage window without the corresponding reconciliation that occurs on success. Because `EnergyProcessor`/`resetAccountUsage` math is area-based (usage × window size), a leftover un-reconciled merge compounds with each subsequent transaction's own merge, permanently skewing the account's (and, when creator ≠ caller for delegated `TriggerSmartContract` calls, third-party delegators') available frozen energy computation on future transactions.

### Likelihood Explanation
This requires only a normal, unprivileged transaction: crafting a `CreateSmartContract` whose constructor bytecode reverts deterministically (e.g., `REVERT` opcode, or an out-of-gas/exception path) is trivial and fully attacker-controlled, and `StorageUtils.getEnergyLimitHardFork()` / `VMConfig.allowTvmFreezeV2()` / `dynamicPropertiesStore.supportUnfreezeDelay()` are already-activated protocol features on any network running the FreezeV2/unfreeze-delay hard fork, not admin-gated at attack time. The attack is fully repeatable by resubmitting reverting creation transactions from the same account.

### Recommendation
Make the `resetAccountUsage` decision in `pay()` symmetric with the pre-merge write in `VMActuator`: reconcile (undo) the merged usage/window whenever a pre-merge occurred in `validate()`, independent of whether `execute()` later reverts/throws, or alternatively perform the energy-limit pre-merge write only after execution outcome is known (post-revert-check) so that failed/reverted creations never leave a dangling merge in the account's window state.

### Proof of Concept
```java
// Integration test sketch, extending TransactionTraceTest
@Test
public void testRevertingCreationLeavesUnreconciledEnergyWindow() throws Exception {
  dbManager.getDynamicPropertiesStore().saveUnfreezeDelayDays(14); // enable supportUnfreezeDelay()
  // deploy a constructor that unconditionally executes REVERT
  String revertingCode = "<bytecode with REVERT opcode in constructor>";
  CreateSmartContract revertingContract = TvmTestUtils.createSmartContract(
      Commons.decodeFromBase58Check(OwnerAddress), "reverter", ABI, revertingCode, 0, 100);
  Transaction tx1 = /* build CreateSmartContract tx from OwnerAddress */;
  TransactionCapsule txCap1 = new TransactionCapsule(tx1);
  TransactionTrace trace1 = new TransactionTrace(txCap1, StoreFactory.getInstance(), new RuntimeImpl());
  trace1.init(null);
  trace1.exec();
  Assert.assertTrue(trace1.getRuntimeResult().isRevert()
      || trace1.getRuntimeResult().getException() != null);
  trace1.pay(); // resetAccountUsage skipped, payEnergyBill still runs

  AccountCapsule afterRevert = dbManager.getAccountStore()
      .get(Commons.decodeFromBase58Check(OwnerAddress));
  long usageAfterRevert = afterRevert.getEnergyUsage();
  long windowAfterRevert = afterRevert.getWindowSize(ENERGY);

  // A reference model that always reconciles regardless of revert would produce
  // usage/window equal to a baseline where merge is always undone. Assert divergence:
  Assert.assertNotEquals(expectedReconciledUsage, usageAfterRevert);

  // Successful tx from same account afterwards re-merges on top of unreconciled state
  CreateSmartContract okContract = TvmTestUtils.createSmartContract(
      Commons.decodeFromBase58Check(OwnerAddress), "ok", ABI, OK_CODE, 0, 100);
  Transaction tx2 = /* build CreateSmartContract tx from OwnerAddress */;
  TransactionCapsule txCap2 = new TransactionCapsule(tx2);
  TransactionTrace trace2 = new TransactionTrace(txCap2, StoreFactory.getInstance(), new RuntimeImpl());
  trace2.init(null);
  trace2.exec();
  trace2.pay();

  AccountCapsule finalAccount = dbManager.getAccountStore()
      .get(Commons.decodeFromBase58Check(OwnerAddress));
  // Compare finalAccount.getEnergyUsage()/getWindowSize(ENERGY) against a reference
  // model that always reconciles (undoes the pre-merge) regardless of revert outcome.
  Assert.assertNotEquals(referenceModelFinalUsage, finalAccount.getEnergyUsage());
}
```
Expected result: the reverted transaction leaves the account's `EnergyUsage`/`WindowSize` at the merged (unreconciled) value instead of restoring the pre-transaction state, and the subsequent successful transaction's merge compounds on top of it, diverging from a reference model that always reconciles on revert.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L234-238)
```java
    switch (trxType) {
      case TRX_CONTRACT_CREATION_TYPE:
        callerAccount = trx.getOwnerAddress();
        originAccount = callerAccount;
        break;
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L261-280)
```java
    if (dynamicPropertiesStore.supportUnfreezeDelay()
        && getRuntimeResult().getException() == null && !getRuntimeResult().isRevert()) {

      // just fo caller is not origin, we set the related field for origin account
      if (origin != null && !caller.getAddress().equals(origin.getAddress())) {
        resetAccountUsage(origin,
            receipt.getOriginEnergyUsage(),
            receipt.getOriginEnergyWindowSize(),
            receipt.getOriginEnergyMergedUsage(),
            receipt.getOriginEnergyMergedWindowSize(),
            receipt.getOriginEnergyWindowSizeV2());
      }

      resetAccountUsage(caller,
          receipt.getCallerEnergyUsage(),
          receipt.getCallerEnergyWindowSize(),
          receipt.getCallerEnergyMergedUsage(),
          receipt.getCallerEnergyMergedWindowSize(),
          receipt.getCallerEnergyWindowSizeV2());
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L281-288)
```java
    receipt.payEnergyBill(
        dynamicPropertiesStore, accountStore, forkController,
        origin,
        caller,
        percent, originEnergyLimit,
        energyProcessor,
        EnergyProcessor.getHeadSlot(dynamicPropertiesStore));
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L290-308)
```java
  private void resetAccountUsage(AccountCapsule accountCap,
      long usage, long size, long mergedUsage, long mergedSize, long size2) {
    if (dynamicPropertiesStore.supportAllowCancelAllUnfreezeV2()) {
      resetAccountUsageV2(accountCap, usage, size, mergedUsage, mergedSize, size2);
      return;
    }
    long currentSize = accountCap.getWindowSize(ENERGY);
    long currentUsage = accountCap.getEnergyUsage();
    // Drop the pre consumed frozen energy
    long newArea = currentUsage * currentSize
        - (mergedUsage * mergedSize - usage * size);
    // If area merging happened during suicide, use the current window size
    long newSize = mergedSize == currentSize ? size : currentSize;
    // Calc new usage by fixed x-axes
    long newUsage = max(0, newArea / newSize, dynamicPropertiesStore.disableJavaLangMath());
    // Reset account usage and window size
    accountCap.setEnergyUsage(newUsage);
    accountCap.setNewWindowSize(ENERGY, newUsage == 0 ? 0L : newSize);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L583-601)
```java
    if (VMConfig.allowTvmFreezeV2()) {
      long now = rootRepository.getHeadSlot();
      EnergyProcessor energyProcessor =
          new EnergyProcessor(
              rootRepository.getDynamicPropertiesStore(),
              ChainBaseManager.getInstance().getAccountStore());
      energyProcessor.updateUsage(account);
      account.setLatestConsumeTimeForEnergy(now);
      receipt.setCallerEnergyUsage(account.getEnergyUsage());
      receipt.setCallerEnergyWindowSize(account.getWindowSize(ENERGY));
      receipt.setCallerEnergyWindowSizeV2(account.getWindowSizeV2(ENERGY));
      account.setEnergyUsage(
          energyProcessor.increase(account, ENERGY,
              account.getEnergyUsage(), min(leftFrozenEnergy, energyFromFeeLimit,
                  VMConfig.disableJavaLangMath()), now, now));
      receipt.setCallerEnergyMergedUsage(account.getEnergyUsage());
      receipt.setCallerEnergyMergedWindowSize(account.getWindowSize(ENERGY));
      rootRepository.updateAccount(account.createDbKey(), account);
    }
```
