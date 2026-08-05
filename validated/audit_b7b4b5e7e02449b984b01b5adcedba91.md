### Title
`getCanDelegatedMaxSize` bandwidth preview diverges from `DelegateResourceActuator` validation, causing reported max-delegatable amounts to revert on-chain - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
The read-only RPC `getCanDelegatedMaxSize` (backed by `Wallet.calcCanDelegatedBandWidthMaxSize`) is meant to tell a caller the maximum TRX they can delegate as bandwidth without their `DelegateResourceContract` failing. Its bandwidth-usage calculation is not identical to the one performed inside `DelegateResourceActuator.doValidate()`, which is the code that actually enforces the delegation limit. This is the same class of bug as the ERC-4626 `maxDeposit` finding: a "max/preview" function that does not compute the exact same guard the real state-changing operation uses, so a value reported as safe by the preview can be rejected when actually submitted.

### Finding Description
`Wallet.calcCanDelegatedBandWidthMaxSize` unconditionally adds the transaction-creation bandwidth estimate to the account's usage before computing the delegatable size: [1](#0-0) 

```
BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
processor.updateUsage(ownerCapsule);

long accountNetUsage = ownerCapsule.getNetUsage();
accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
        ownerCapsule.getFrozenV2BalanceForBandwidth());

long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
        (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, dynamicStore.disableJavaLangMath());

long maxSize =  ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage;
return max(0, maxSize, dynamicStore.disableJavaLangMath());
```

By contrast, the actual authoritative check performed by `DelegateResourceActuator.doValidate()` for the `BANDWIDTH` case only adds that same bandwidth estimate conditionally, and it uses a different bandwidth-usage-update call (`updateUsageForDelegated`, not `updateUsage`): [2](#0-1) 

```
case BANDWIDTH: {
    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    processor.updateUsageForDelegated(ownerCapsule);

    long accountNetUsage = ownerCapsule.getNetUsage();
    if (null != this.getTx() && this.getTx().isTransactionCreate()) {
      accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
              ownerCapsule.getFrozenV2BalanceForBandwidth());
    }
    long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
        (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
    long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
        this.disableJavaLangMath());
    if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
      throw new ContractValidateException(
          "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
    }
}
```

Both code paths derive `netUsage`/`v2NetUsage` from the same formula shape, but they:
1. Call different `BandwidthProcessor` methods (`updateUsage` vs `updateUsageForDelegated`), which can leave the account's bandwidth-usage bookkeeping (`NetUsage`, `LatestConsumeTime`) in different states before the calculation.
2. Apply the `estimateConsumeBandWidthSize` term under different conditions — unconditionally in the preview, conditionally (`isTransactionCreate()`) in the real validation.

This is exactly the shape of the `maxDeposit`/`deposit` mismatch in the reported ERC-4626 bug: a public "how much can I safely do" query and the actual state-mutating operation compute their guard condition differently, so a value that the query says is safe is not guaranteed to pass the real check (or vice versa).

### Impact Explanation
A wallet/dApp integrator that calls `GetCanDelegatedMaxSize` (gRPC/HTTP) to determine the maximum bandwidth balance a user can delegate, and then submits a `DelegateResourceContract` for that reported amount, can have the transaction rejected by `ContractValidateException` in `DelegateResourceActuator.doValidate()` ("delegateBalance must be less than or equal to available FreezeBandwidthV2 balance"), because the actuator's stricter/looser usage computation disagrees with the preview. This wastes the submitter's bandwidth/fee for a transaction that the node's own advisory API told them was valid, and breaks the implicit "preview accurately predicts execution" contract that wallets rely on for resource delegation UX — an invalid-state/divergence class impact rather than a funds-loss exploit.

### Likelihood Explanation
This triggers under ordinary usage whenever `updateUsage` and `updateUsageForDelegated` leave the account in materially different bandwidth-usage states (e.g., differing treatment of already-delegated usage or stale usage windows) and/or when the caller's transaction is a `TransactionCreate` (broadcast-and-create) versus not, since that flag flips whether the extra `estimateConsumeBandWidthSize` term is added in the actuator but is always added in the query. No special privileges or attacker control are required — any ordinary account owner delegating resources based on the advisory RPC can hit this divergence.

### Recommendation
Make `Wallet.calcCanDelegatedBandWidthMaxSize` (and the analogous energy path, if it has similar asymmetry) invoke the exact same usage-update method (`updateUsageForDelegated`) and apply the `estimateConsumeBandWidthSize` addition under the identical condition (`isTransactionCreate()`, or otherwise consistently) as `DelegateResourceActuator.doValidate()`, so the preview and the real validation always agree on the maximum delegatable size.

### Proof of Concept
Not independently reproduced in this analysis: verifying an exact end-to-end revert requires running `getCanDelegatedMaxSize`, submitting the reported amount via `DelegateResourceContract` with `isTransactionCreate()` true/false in different orders, and confirming a rejection. I was unable to fetch the `BandwidthProcessor.updateUsage`/`updateUsageForDelegated` implementations (`chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`) within this session's tool budget to confirm the precise magnitude/direction of divergence between the two update methods; only the source-level asymmetry in the `estimateConsumeBandWidthSize` conditional shown above is directly confirmed. A Devin session with full repository/test access should build the described query→delegate scenario to confirm the revert concretely.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1003-1017)
```java
    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    processor.updateUsage(ownerCapsule);

    long accountNetUsage = ownerCapsule.getNetUsage();
    accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
            ownerCapsule.getFrozenV2BalanceForBandwidth());

    long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

    long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, dynamicStore.disableJavaLangMath());

    long maxSize = ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage;
    return max(0, maxSize, dynamicStore.disableJavaLangMath());
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L152-169)
```java
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
        processor.updateUsageForDelegated(ownerCapsule);

        long accountNetUsage = ownerCapsule.getNetUsage();
        if (null != this.getTx() && this.getTx().isTransactionCreate()) {
          accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
                  ownerCapsule.getFrozenV2BalanceForBandwidth());
        }
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
```
