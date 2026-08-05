### Title
Unsynced cached `AcquiredDelegatedFrozenBalance` accounting causes permanent unfreeze DoS on legacy V1 delegated resources - (File: actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java)

### Summary
`UnfreezeBalanceActuator` (the legacy stake-1.0 unfreeze path) validates a delegator's unfreeze request against a cached counter, `AcquiredDelegatedFrozenBalanceForBandwidth`/`ForEnergy`, stored on the *receiver's* account. This counter is bookkeeping that mirrors the `DelegatedResourceCapsule` balance, exactly analogous to the ERC4626 report's `pendingRevenue` snapshot mirroring vault asset value. When the receiver account is deleted and recreated (e.g., a TVM contract self-destructs and a new account is later created at the same address, or `CREATE2` redeploy), this cached counter resets/drifts independently of the still-existing `DelegatedResourceCapsule` record — the accounting outflow (account deletion) is not reflected in the cached value. Unlike the newer stake-2.0 paths, which explicitly detect and repair this drift by resetting the counter to 0, the legacy V1 `validate()` path has no such repair and unconditionally reverts, permanently blocking the delegator from unfreezing/withdrawing their own principal.

### Finding Description
In `UnfreezeBalanceActuator.doValidate()`, for the delegated (non-self) unfreeze path: [1](#0-0) 

the check `receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() < delegatedResourceCapsule.getFrozenBalanceForBandwidth()` throws a `ContractValidateException` if the cached value is smaller than the real delegated amount — with no fallback. The same pattern exists for `ENERGY` a few lines below.

`AcquiredDelegatedFrozenBalanceForBandwidth`/`ForEnergy` are per-account cached fields (protobuf `account_resource.acquired_delegated_frozen_balance_for_energy`, etc.) maintained separately from the canonical `DelegatedResourceCapsule` store entry: [2](#0-1) 

They function exactly like the report's `pendingRevenue`/`lastTotalAssets` snapshot: a derived accounting value that must stay in sync with the ground-truth record (`DelegatedResourceCapsule`) but is only updated through specific mutation paths (freeze/delegate/undelegate). If the receiver account is deleted (e.g. contract self-destruct) and later recreated with a different/default `AcquiredDelegatedFrozenBalance`, the cached value silently diverges from the real delegated balance recorded in `DelegatedResourceCapsule`, which is untouched by account deletion.

The codebase itself acknowledges this exact class of drift in the newer, stake-2.0 equivalent code, where the fix is to defensively reset the counter instead of reverting: [3](#0-2) 

and in `UnDelegateResourceActuator`: [4](#0-3) 

But the legacy V1 `UnfreezeBalanceActuator` path that reaches the `AcquiredDelegatedFrozenBalanceForBandwidth`/`ForEnergy` comparison in `validate()` has no equivalent reset — it simply reverts the owner's unfreeze transaction. This exact drift-and-revert scenario is reproduced in the existing test suite: [5](#0-4) 

which shows `actuator.validate()` throwing `"AcquiredDelegatedFrozenBalanceForEnergy[10] < delegatedEnergy[1000000000]"` after the receiver account is deleted/recreated with a smaller cached counter — and only succeeds once `AllowShieldedTransaction`/`AllowTvmSolidity059` are additionally toggled to route around the vulnerable check.

### Impact Explanation
This breaks the same guarantee described in the report: a cached accounting snapshot that isn't refreshed on all paths that change the underlying real balance causes the accounting-consuming operation (here, the owner's unfreeze/withdraw of frozen TRX) to fail. Unlike the ERC4626 report where only "revenue" (yield) is stuck, here the stuck asset is the **delegator's own frozen TRX principal** — an owner who delegated stake-1.0 bandwidth/energy to a receiver address that is later deleted and recreated with a mismatched cached counter is permanently blocked from calling `UnfreezeBalanceContract` for that delegation via the vulnerable code path, since `validate()` always throws before `execute()` can run. This is a concrete denial-of-service on a fund-withdrawal state transition.

### Likelihood Explanation
Reachable by any unprivileged account: delegating resources to a contract address that later self-destructs (and can be recreated via `CREATE2` at the same address, a capability the codebase itself repeatedly guards against elsewhere) is a normal, permissionless flow. The severity is reduced by the guard clauses added for `AllowTvmConstantinople`/`AllowTvmSolidity059`/receiver account type, which can route around the unconditional check in some configurations, and by V1 stake being legacy functionality superseded by stake-2.0 delegation (which has the fix). However, the codebase's own defensive-reset pattern in the V2 equivalent confirms the underlying accounting drift is real and was only partially remediated for the newer path.

### Recommendation
Apply the same defensive-reset logic used in `UnDelegateResourceProcessor`/`UnDelegateResourceActuator` to the legacy `UnfreezeBalanceActuator` validate/execute paths: when `receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()/ForEnergy()` is less than the recorded `DelegatedResourceCapsule` balance, clamp/reset the cached counter to the correct floor (e.g., 0) instead of reverting, so the delegator can always reclaim their own frozen balance regardless of the receiver account's lifecycle.

### Proof of Concept
1. Owner delegates BANDWIDTH/ENERGY to receiver `R` via `FreezeBalanceContract` (V1, `supportDR()`), setting `DelegatedResourceCapsule.frozenBalanceForEnergy` and `R.acquiredDelegatedFrozenBalanceForEnergy` to `X`.
2. `R` is a contract account that self-destructs (or is otherwise deleted) and is later recreated as a normal/contract account with `acquiredDelegatedFrozenBalanceForEnergy` less than `X` (default 0, or partially consumed) — demonstrated directly in `UnfreezeBalanceActuatorTest.testUnfreezeDelegatedBalanceForCpuWithRecreatedReceiver` (lines 747-822).
3. Owner calls `UnfreezeBalanceContract` to reclaim the frozen TRX for that delegation.
4. `doValidate()` at `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java:374-393` throws `ContractValidateException("AcquiredDelegatedFrozenBalanceForEnergy[...] < delegatedEnergy[...]")`, permanently blocking the unfreeze under the unconditional/legacy-guard branch, while the on-chain `DelegatedResourceCapsule` record persists unchanged, freezing the owner's TRX indefinitely through this path.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L373-393)
```java
          if (dynamicStore.getAllowTvmConstantinople() == 0) {
            if (receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < delegatedResourceCapsule.getFrozenBalanceForBandwidth()) {
              throw new ContractValidateException(
                  "AcquiredDelegatedFrozenBalanceForBandwidth[" + receiverCapsule
                      .getAcquiredDelegatedFrozenBalanceForBandwidth() + "] < delegatedBandwidth["
                      + delegatedResourceCapsule.getFrozenBalanceForBandwidth()
                      + "]");
            }
          } else {
            if (dynamicStore.getAllowTvmSolidity059() != 1
                && receiverCapsule != null
                && receiverCapsule.getType() != AccountType.Contract
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < delegatedResourceCapsule.getFrozenBalanceForBandwidth()) {
              throw new ContractValidateException(
                  "AcquiredDelegatedFrozenBalanceForBandwidth[" + receiverCapsule
                      .getAcquiredDelegatedFrozenBalanceForBandwidth() + "] < delegatedBandwidth["
                      + delegatedResourceCapsule.getFrozenBalanceForBandwidth()
                      + "]");
            }
```

**File:** protocol/src/main/protos/core/Tron.proto (L204-207)
```text
    //Frozen balance provided by other accounts to this account
    int64 acquired_delegated_frozen_balance_for_energy = 4;
    //Frozen balances provided to other accounts
    int64 delegated_frozen_balance_for_energy = 5;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L107-113)
```java
          /* For example, in a scenario where a regular account can be upgraded to a contract
          account through an interface, the account information will be cleared after the
          contract suicide, and this account will be converted to a regular account in the future */
          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L75-78)
```java
          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
```

**File:** framework/src/test/java/org/tron/core/actuator/UnfreezeBalanceActuatorTest.java (L783-802)
```java
    dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(0);
    dbManager.getDynamicPropertiesStore().saveAllowTvmSolidity059(0);
    dbManager.getAccountStore().delete(receiver.createDbKey());
    receiver = new AccountCapsule(receiver.getAddress(), ByteString.EMPTY, AccountType.Normal);
    receiver.setAcquiredDelegatedFrozenBalanceForEnergy(10L);
    dbManager.getAccountStore().put(receiver.createDbKey(), receiver);
    receiver = dbManager.getAccountStore().get(receiver.createDbKey());
    Assert.assertEquals(10, receiver.getAcquiredDelegatedFrozenBalanceForEnergy());

    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.fail();
    } catch (ContractValidateException e) {
      Assert.assertEquals(
          "AcquiredDelegatedFrozenBalanceForEnergy[10] < delegatedEnergy[1000000000]",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.fail();
    }
```
