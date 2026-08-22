### Title
Delegated resource state left un-accrued for the receiver in `DelegateResourceActuator`, unlike the symmetric `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` paths - ([File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java])

### Summary
This maps to the same bug class as the external report: one code path "accrues" (settles) the resource-usage state of an account to the current time before mutating its balance/usage fields, while a parallel/symmetric path skips that accrual step for the counter-party account, producing an inconsistent on-chain state between the two accounts involved in the same logical operation.

### Finding Description
When a user calls `DelegateResourceActuator.validate()`/`execute()` to delegate bandwidth or energy to a `receiverAddress`, the owner side is properly "accrued": `validate()` explicitly calls `BandwidthProcessor.updateUsageForDelegated(ownerCapsule)` or `EnergyProcessor.updateUsage(ownerCapsule)` before computing how much of the owner's frozen balance is still usable for delegation. [1](#0-0) 

However, the receiver side is mutated directly, with no equivalent usage-accrual call, inside the private `delegateResource` helper invoked from `execute()`: [2](#0-1) 

This is asymmetric with the reverse operation. `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` explicitly call `bandwidthProcessor.updateUsageForDelegated(receiverCapsule)` and `energyProcessor.updateUsage(receiverCapsule)` on the receiver **before** any of the receiver's usage/acquired-balance fields are changed: [3](#0-2) 

So on `delegate`, `receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth/Energy` is bumped while `receiverCapsule`'s `NetUsage`/`EnergyUsage`/`LatestConsumeTime` fields remain stale (as of whenever the receiver last consumed resources), whereas on `undelegate` the receiver's usage is first decayed/settled to "now" via the processor before the acquired balance is changed. This mirrors exactly the reported pattern: the state-settling function (`accrue_interest` / here `updateUsageForDelegated`/`updateUsage`) is called for one side of a two-party operation but omitted for the other side in a sibling entrypoint that otherwise looks symmetric.

### Impact Explanation
Because `receiverCapsule`'s usage window is not decayed to the current block time when new delegated resource is granted, the receiver's subsequent bandwidth/energy consumption calculations (window size, remaining usage, resource limit checks in `ResourceProcessor.increase`) are computed against a stale `LatestConsumeTime`/usage baseline instead of the value they would have if properly time-decayed first. This is a resource-accounting inconsistency: depending on how usage decay interacts with the newly added `AcquiredDelegatedFrozenV2Balance`, this can let a receiver retain higher-than-intended usage debt or, conversely, get its usage window silently reset/miscalculated relative to what `UnDelegateResourceProcessor`'s symmetric call would produce — a divergence from the intended resource-accounting invariant that a receiver's usage state is always current before any change to its delegated resource entitlement.

### Likelihood Explanation
`DelegateResourceContract` is a normal, publicly reachable transaction type (`ContractType.DelegateResourceContract`) that any account can broadcast against any other non-contract receiver address once `supportDR`/`supportUnfreezeDelay` are enabled on-chain, requiring no privileged role — any user can trigger this path by delegating resources to any account, including repeatedly delegating/undelegating to manipulate the receiver's usage/window state.

### Recommendation
In `DelegateResourceActuator.delegateResource` (and the corresponding TVM-native `DelegateResourceProcessor.execute`), settle the receiver's usage state before mutating its acquired delegated balance, mirroring the un-delegate path: call `new BandwidthProcessor(chainBaseManager).updateUsageForDelegated(receiverCapsule)` for `BANDWIDTH` and `new EnergyProcessor(dynamicPropertiesStore, accountStore).updateUsage(receiverCapsule)` for `ENERGY` immediately before `receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth/Energy(balance)` is applied and before `accountStore.put(receiverCapsule.createDbKey(), receiverCapsule)`.

### Proof of Concept
Not executable from the index alone (no local build/test harness available in this session). Conceptually: 
1. Account A freezes TRX for bandwidth/energy (V2) and delegates to Account B (`DelegateResourceContract`) — B's `NetUsage`/`EnergyUsage`/`LatestConsumeTime` are left untouched even though B may have consumed resources long ago.
2. B consumes bandwidth/energy using the newly delegated resource; because B's usage window was never decayed at delegation time, the effective usage/limit calculation differs from the value that would result if `updateUsageForDelegated`/`updateUsage` had been called first (as happens on `UnDelegateResourceContract`).
3. Repeating delegate/undelegate cycles between A and B, or comparing against `UnDelegateResourceActuator`'s behavior for the same receiver, should reveal a divergent usage/window value depending on which path (delegate vs. undelegate) is used to touch the receiver account, demonstrating the accounting inconsistency described above.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L152-189)
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
      }
      break;
      case ENERGY: {
        EnergyProcessor processor = new EnergyProcessor(dynamicStore, accountStore);
        processor.updateUsage(ownerCapsule);

        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (dynamicStore.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));
        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L317-325)
```java
    //modify AccountStore for receiver
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(balance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(balance);
    }
    accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L100-127)
```java
    long transferUsage = 0;
    // modify receiver Account
    if (receiverCapsule != null) {
      switch (param.getResourceType()) {
        case BANDWIDTH:
          BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(ChainBaseManager.getInstance());
          bandwidthProcessor.updateUsageForDelegated(receiverCapsule);
          /* For example, in a scenario where a regular account can be upgraded to a contract
          account through an interface, the account information will be cleared after the
          contract suicide, and this account will be converted to a regular account in the future */
          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
```
