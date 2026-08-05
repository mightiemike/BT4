### Title
Inconsistent accounting of bandwidth "already-used" size when computing delegatable balance across three code paths - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
Analogous to the Gondi `undeployedAssets` bug — where the same conceptual "liquid/undeployed" quantity was computed with different formulas in `_getUndeployedAssets()`, `validateOffer`, and `_reallocate()` — java-tron computes the "available balance to delegate for BANDWIDTH" with three different formulas across `Wallet.calcCanDelegatedBandWidthMaxSize()`, `DelegateResourceActuator.validate()`, and `DelegateResourceProcessor.validate()` (TVM native contract path). One path unconditionally adds a synthetic "estimated new-account bandwidth cost" term, another adds it only conditionally, and the TVM path omits it entirely.

### Finding Description
`Wallet.calcCanDelegatedBandWidthMaxSize()` computes the maximum amount an account can delegate for bandwidth like this: [1](#0-0) 
It **unconditionally** adds `TransactionUtil.estimateConsumeBandWidthSize(dynamicStore, ownerCapsule.getFrozenV2BalanceForBandwidth())` to `accountNetUsage` before converting it to a "v2" usage figure and subtracting it from the frozen balance to derive `maxSize`.

Contrast this with `DelegateResourceActuator.validate()`, which performs the actual on-chain validation when a user submits a real `DelegateResourceContract` transaction: [2](#0-1) 
Here the same `estimateConsumeBandWidthSize` term is added **only conditionally** — `if (null != this.getTx() && this.getTx().isTransactionCreate())` — meaning for most normal delegate transactions this extra deduction is skipped entirely.

A third path, `DelegateResourceProcessor.validate()` (used when a smart contract invokes the native `delegateResource` precompile/opcode via TVM), computes the check without ever adding this term: [3](#0-2) 

All three paths purport to answer the identical question — "how much frozen-for-bandwidth balance is actually free to delegate right now" — yet use three different formulas for the deducted "usage" term. This is structurally the same bug class as the Gondi finding: a single accounting concept (`undeployedAssets`/"available delegatable balance") is computed inconsistently depending on which code path evaluates it, because one or more call sites forget to include (or wrongly include) a term that the "canonical" calculation (`_getUndeployedAssets()` / here, the public-facing `Wallet.calcCanDelegatedBandWidthMaxSize`) accounts for.

### Impact Explanation
The impact here is a **query/validation divergence**, not direct fund loss: the public-facing read-only RPC/HTTP API `getCanDelegatedMaxSize` (backed by `calcCanDelegatedBandWidthMaxSize`) can report a smaller "maximum delegatable" value than what the actual `DelegateResourceActuator.validate()` will accept for a regular transaction, and the TVM native-contract path (`DelegateResourceProcessor`) is more permissive still (never applying the deduction). This means:
- Wallets/dApps relying on the query API to pre-compute a safe delegate amount get an overly conservative (or, depending on state, an inconsistent) answer relative to what the chain will actually execute.
- Contracts calling `delegateResource` via TVM can delegate resource balance that the "canonical" accounting method would have blocked, because the TVM path never reserves the `estimateConsumeBandWidthSize` buffer that the other two paths (partially) reserve.
- This is an invalid-state/divergence issue between advertised (queryable) state and actually enforced consensus state, matching the "inconsistent accounting → incorrect checks" pattern of the referenced report, though the direct financial impact is limited to bandwidth resource accounting rather than protocol asset custody.

### Likelihood Explanation
This triggers deterministically and unprivileged for any account with a non-zero `FrozenV2BalanceForBandwidth` calling any of the three code paths — no special privileges are required, only ordinary `DelegateResourceContract` transactions, smart contract calls to the delegate-resource precompile, or the public `getCanDelegatedMaxSize` API. Given the differing conditionals (`isTransactionCreate()`), the actual divergence magnitude depends on transaction context, but the structural inconsistency itself is unconditional and always reachable.

### Recommendation
Unify the three implementations to compute the "usage to reserve" term identically:
- Decide the correct semantics once (should the estimated bandwidth cost of an implicit new-account creation be included when computing delegatable balance?), and apply the same condition/formula in `Wallet.calcCanDelegatedBandWidthMaxSize()`, `DelegateResourceActuator.validate()`, and `DelegateResourceProcessor.validate()`.
- Factor the shared logic into a single static/shared method (similar to `FreezeV2Util.getV2NetUsage`) that all three call sites invoke, eliminating the possibility of the formulas drifting apart again.

### Proof of Concept
1. Freeze bandwidth for account A via `FreezeBalanceV2Contract`, giving `FrozenV2BalanceForBandwidth = X`.
2. Call the HTTP/gRPC `getCanDelegatedMaxSize` endpoint (→ `Wallet.calcCanDelegatedBandWidthMaxSize`) — observe `maxSize = X - v2NetUsage_with_estimate` (the estimate term is always subtracted). [4](#0-3) 
3. Submit a `DelegateResourceContract` transaction from A delegating `X - v2NetUsage_without_estimate` (a larger amount than step 2 reported as the max, but still ≤ what `DelegateResourceActuator.validate()` permits since `isTransactionCreate()` is false for a normal delegate call) — the transaction succeeds, exceeding what the query API said was delegatable. [5](#0-4) 
4. Alternatively, deploy a contract that calls the native `delegateResource` operation for account A and observe that `DelegateResourceProcessor.validate()` permits delegating the full `X - v2NetUsage` with no estimate deduction at all, diverging further from both other paths. [6](#0-5)

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

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L153-169)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L58-72)
```java
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(ChainBaseManager.getInstance());
        processor.updateUsageForDelegated(ownerCapsule);

        long netUsage = (long) (ownerCapsule.getNetUsage() * TRX_PRECISION * ((double)
            (repo.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
      }
```
