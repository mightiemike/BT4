### Title
Repeated dust `UnDelegateResourceContract` calls let an attacker launder Energy/Bandwidth usage debt through floor-rounding of the proportional `transferUsage` split - (File: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java`)

### Summary
The Resource-Delegation (`Stake 2.0`) unwind path computes how much accumulated `EnergyUsage`/`NetUsage` must move from the resource receiver back to the resource owner when a delegation is partially revoked. This `transferUsage` value is computed with floating-point arithmetic and truncated (`(long)` cast, i.e. floor toward zero) exactly like the fixed-rate fee in the referenced report. Because the truncation happens per call and is proportional to the tiny `unDelegateBalance` requested, an attacker can split a single large `UnDelegateResourceContract` into many dust-sized calls so that `transferUsage` rounds to `0` on every call, while the full resource balance is still reclaimed. The receiver keeps its full energy/bandwidth usage debt with zero remaining backing balance, and the owner reclaims the entire delegated balance with zero usage debt attached — effectively "washing" consumed, metered resource for free, the same rounding-exploited-by-repetition pattern described in the M-3 report.

### Finding Description
`UnDelegateResourceActuator.execute` (and its TVM analog `UnDelegateResourceProcessor.execute`) computes: [1](#0-0) 

```
long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
    * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
transferUsage = (long) (receiverCapsule.getEnergyUsage()
    * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
transferUsage = min(unDelegateMaxUsage, transferUsage);
```
identical logic exists for `BANDWIDTH` in the same file, and duplicated in the TVM-callable native contract processor: [2](#0-1) 

`transferUsage` is the fraction of the receiver's currently used Energy that should be "clawed back" and attributed to the owner, proportional to the fraction of the total delegated balance being un-delegated. This is analogous to the fee/discount computed proportionally to a shared pool in the referenced report. The result is truncated to a `long`, so for small `unDelegateBalance` relative to `receiverCapsule.getAllFrozenBalanceForEnergy()`, the product rounds down to `0`.

Both `UnDelegateResourceContract` (a normal broadcast transaction type, validated in `UnDelegateResourceActuator.validate`) and the TVM precompiled `unDelegateResource` (reachable from any deployed contract, validated in `UnDelegateResourceProcessor.validate`) only require `unDelegateBalance > 0` and sufficient delegated balance — there is no minimum bound preventing dust amounts: [3](#0-2) 

By splitting one large un-delegation into `N` dust-sized `unDelegateBalance` calls (e.g. 1 SUN each), each call computes `transferUsage = 0` (rounded down), yet each call still fully:
- decrements `receiverCapsule`'s `AcquiredDelegatedFrozenV2BalanceForEnergy` by the dust amount,
- increments the owner's `FrozenV2BalanceForEnergy` by the dust amount,
- leaves the receiver's `EnergyUsage` completely untouched.

After all `N` dust calls sum to the full originally delegated balance, the receiver has lost 100% of the backing frozen balance for energy but still retains its entire pre-existing `EnergyUsage` debt, while the owner has reclaimed the full balance with zero attributed usage. This breaks the invariant enforced by `ResourceProcessor.unDelegateIncrease`/`unDelegateIncreaseV2`, which is designed to keep the ratio of `usage : windowSize` consistent between owner and receiver as balance moves: [4](#0-3) 

The owner is thus able to acquire back its full stake with a "clean" (unused) energy allowance while the receiver silently absorbs usage that should have migrated with the balance — the receiver's usage-to-limit ratio becomes artificially worse (or its window size becomes inconsistent), while the owner's newly reclaimed resource is granted without the debt that should accompany it. If owner and receiver are the same attacker (self-delegate then self-undelegate in dust chunks, a pattern already exercised as valid in the test suite, e.g. `UnDelegateResourceActuatorTest`), this lets the attacker effectively reset/launder consumed Energy accounting for free, obtaining more usable Energy capacity than their frozen TRX weight legitimately entitles them to under `TotalEnergyCurrentLimit`/`TotalEnergyWeight` — the shared, chain-wide metered resource pool.

### Impact Explanation
Energy and Bandwidth are metered, chain-wide shared resources whose total issuance is capped by `TotalEnergyCurrentLimit`/`TotalNetLimit` and apportioned by `TotalEnergyWeight`/`TotalNetWeight` (frozen TRX). This bug allows an attacker to desynchronize the usage-to-balance accounting between two accounts they control (owner/receiver pair) via a purely mechanical, permissionless resource-delegation transaction (`UnDelegateResourceContract`, or the equivalent TVM precompile callable from any smart contract). Repeated dust un-delegations let the attacker reclaim delegated TRX weight without correspondingly reclaiming the usage debt, corrupting the energy/bandwidth accounting invariant and letting the attacker obtain effectively free/extra resource capacity relative to their legitimate stake — a resource/asset accounting corruption analogous to stealing from the shared "unassigned earnings" pool in the source report. This does not directly move TRX balances but corrupts a value with real economic weight (metered execution capacity), and is reachable by any unprivileged account/contract.

### Likelihood Explanation
The precondition is simply owning (or colluding with) both the delegating owner and the receiving account and calling `UnDelegateResourceContract`/the `unDelegateResource` precompile repeatedly with dust `unDelegateBalance` values — no privileged role, leaked key, or malicious peer is required. It does require many transactions (as in the source report, gas/bandwidth cost scales with the number of dust calls), which bounds profitability but does not prevent the exploit; unlike the referenced protocol, there is no `require(transferUsage > 0)` guard anywhere in `UnDelegateResourceActuator` or `UnDelegateResourceProcessor` to block it.

### Recommendation
Reject (or floor to a minimum) `UnDelegateResourceContract`/`unDelegateResource` calls whose computed `transferUsage` rounds down to `0` while `unDelegateBalance > 0` and `receiverCapsule.getEnergyUsage()/getNetUsage() > 0`, mirroring the report's suggested `require(fee > 0)` mitigation — e.g. require `transferUsage > 0` whenever the receiver has nonzero usage, or compute `transferUsage` with ceiling rounding (as already done for `newOwnerWindowSize` via `divideCeil`/`divideCeilExact` in `ResourceProcessor.unDelegateIncreaseV2`) instead of floor-truncating double-to-long conversion, so that no usage debt can be "erased" through amount-splitting.

### Proof of Concept
1. Account `O` delegates `F` TRX of energy resource to account `R` via `DelegateResourceContract`.
2. `R` consumes energy through normal contract calls until `R.getEnergyUsage() > 0`.
3. `O` issues `N` `UnDelegateResourceContract` transactions, each with `unDelegateBalance = F/N` (dust-sized), until the entire `F` has been undelegated.
4. For sufficiently large `N` (small enough dust amounts relative to `R.getAllFrozenBalanceForEnergy()`), each call's
`transferUsage = (long) (receiverCapsule.getEnergyUsage() * ((double) unDelegateBalance / receiverCapsule.getAllFrozenBalanceForEnergy()))`
rounds down to `0` (see `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java:106-108`).
5. After all `N` calls: `R.AcquiredDelegatedFrozenV2BalanceForEnergy == 0`, `O.FrozenV2BalanceForEnergy == F`, but `R.getEnergyUsage()` is unchanged from step 2 and `O.getEnergyUsage()` received zero transferred usage — i.e., `O` reclaimed the full resource weight with none of the usage debt that a single non-dust `UnDelegateResourceContract(F)` call would have correctly transferred. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L94-116)
```java
        case ENERGY:
          EnergyProcessor energyProcessor = new EnergyProcessor(dynamicStore, accountStore);
          energyProcessor.updateUsage(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy()
              < unDelegateBalance) {
            // A TVM contract receiver, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForEnergy(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
          break;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L68-71)
```java
    long unDelegateBalance = param.getUnDelegateBalance();
    if (unDelegateBalance <= 0) {
      throw new ContractValidateException("unDelegateBalance must be more than 0 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L139-147)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalEnergyCurrentLimit() / repo.getTotalEnergyWeight());
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L190-220)
```java
  public void unDelegateIncrease(AccountCapsule owner, final AccountCapsule receiver,
      long transferUsage, ResourceCode resourceCode, long now) {
    if (dynamicPropertiesStore.supportAllowCancelAllUnfreezeV2()) {
      unDelegateIncreaseV2(owner, receiver, transferUsage, resourceCode, now);
      return;
    }
    long lastOwnerTime = owner.getLastConsumeTime(resourceCode);
    long ownerUsage = owner.getUsage(resourceCode);
    // Update itself first
    ownerUsage = increase(owner, resourceCode, ownerUsage, 0, lastOwnerTime, now);

    long remainOwnerWindowSize = owner.getWindowSize(resourceCode);
    long remainReceiverWindowSize = receiver.getWindowSize(resourceCode);
    remainOwnerWindowSize = remainOwnerWindowSize < 0 ? 0 : remainOwnerWindowSize;
    remainReceiverWindowSize = remainReceiverWindowSize < 0 ? 0 : remainReceiverWindowSize;

    long newOwnerUsage = ownerUsage + transferUsage;
    // mean ownerUsage == 0 and transferUsage == 0
    if (newOwnerUsage == 0) {
      owner.setNewWindowSize(resourceCode, this.windowSize);
      owner.setUsage(resourceCode, 0);
      owner.setLatestTime(resourceCode, now);
      return;
    }
    // calculate new windowSize
    long newOwnerWindowSize = getNewWindowSize(ownerUsage, remainOwnerWindowSize, transferUsage,
        remainReceiverWindowSize, newOwnerUsage);
    owner.setNewWindowSize(resourceCode, newOwnerWindowSize);
    owner.setUsage(resourceCode, newOwnerUsage);
    owner.setLatestTime(resourceCode, now);
  }
```
