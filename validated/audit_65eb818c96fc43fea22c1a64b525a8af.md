### Title
Pro-rata usage transfer on `UnDelegateResource` misattributes bandwidth/energy usage among multiple resource providers - (File: actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java)

### Summary
The reported bug's root cause is a pooled-value pro-rata distribution flaw: a value tied to a specific action/participant (an entrance fee paid by one lender's loan) is spread proportionally over the current total pool instead of being credited exactly to the participant who caused it. The same architectural pattern exists in java-tron's resource-delegation usage accounting: when a delegator un-delegates bandwidth/energy from a receiver that has multiple resource contributors (its own frozen balance plus delegations from possibly several different owners), the usage "returned" to the un-delegating owner is computed as a pro-rata share of the receiver's *total accumulated usage*, based on the ratio of the un-delegated amount to the receiver's *total* frozen balance for that resource — not the usage actually attributable to that specific delegation.

### Finding Description
In `UnDelegateResourceActuator.execute()`, when a resource is un-delegated the code computes: [1](#0-0) 

```java
transferUsage = (long) (receiverCapsule.getNetUsage()
    * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
```

and analogously for energy: [2](#0-1) 

`receiverCapsule.getAllFrozenBalanceForBandwidth()`/`ForEnergy()` represents the *entire pool* backing the receiver's resource limit — the receiver's own frozen balance plus all delegations acquired from any number of distinct owners [3](#0-2) . `receiverCapsule.getNetUsage()`/`getEnergyUsage()` is the receiver's *cumulative consumed usage*, which was accrued over time using whichever balance happened to be available at the time of consumption, without recording which specific delegator's contribution funded a given unit of consumption.

When owner A un-delegates, `transferUsage` is computed purely from the ratio of A's undelegated balance to the *current total* pool (which may now include a different owner B's later delegation), and that usage is then subtracted from the receiver and added back to A via `unDelegateIncrease()`/`unDelegateIncreaseV2()` [4](#0-3) . This exactly mirrors the Sherlock report's flaw: a quantity that should be attributed to a specific contributor (the individual lender's fee, here the individual delegator's actual consumption) is instead distributed pro-rata over the shared pool composed of possibly several contributors who joined/left at different times, so usage gets cross-subsidized between unrelated delegators depending on delegation timing rather than actual causation. The identical pattern is duplicated in the TVM native-contract path `UnDelegateResourceProcessor.execute()` [5](#0-4) .

### Impact Explanation
Because usage attribution is spread over the whole balance pool rather than per-delegation, one delegator can receive/relinquish more or less returned bandwidth/energy usage than they actually consumed, purely as a side effect of other, unrelated delegators joining or leaving the same receiver at different times. Over many delegate/un-delegate cycles across a receiver with multiple resource providers, this misattribution accumulates and results in incorrect resource accounting (usage window sizes and remaining resource limits diverge from the account's true consumption), which is a form of resource/reward accounting corruption analogous to the fee-misdistribution class in the referenced report, though bounded to bandwidth/energy usage bookkeeping rather than direct value transfer.

### Likelihood Explanation
This is triggered by ordinary, permissionless, unprivileged use of `DelegateResourceContract`/`UnDelegateResourceContract` (or the TVM `delegateResource`/`unDelegateResource` opcodes) whenever a receiver account has resources delegated by more than one distinct owner — a normal and common usage pattern (e.g. energy/bandwidth rental services aggregating delegations from many accounts to one receiver contract). No special privilege or malicious peer/node behavior is required; any two unrelated accounts delegating to the same receiver and later un-delegating at different times will exhibit the cross-subsidization.

### Recommendation
Track usage attribution per-delegation (e.g., proportionally scoped to the specific `DelegatedResourceCapsule` between owner and receiver, or using a per-share/"Vi"-style cumulative accounting mechanism similar to the delta-Vi approach already used for vote rewards in `VoteRewardUtil.computeReward()`/`MortgageService.computeReward()`) rather than deriving `transferUsage` from the ratio of the un-delegated amount to the receiver's aggregate pooled frozen balance and aggregate cumulative usage.

### Proof of Concept
1. Owner A delegates 1,000 TRX of bandwidth to receiver R (`DelegateResourceActuator`), becoming R's only resource provider.
2. R consumes a large amount of bandwidth funded entirely by A's delegation, so `R.getNetUsage()` is high relative to `R.getAllFrozenBalanceForBandwidth()`.
3. Owner B then delegates 1,000 TRX of bandwidth to the same receiver R, doubling `R.getAllFrozenBalanceForBandwidth()`, without R consuming any additional bandwidth attributable to B's contribution.
4. B immediately un-delegates their 1,000 TRX (`UnDelegateResourceActuator`). Per [1](#0-0) , `transferUsage` is computed as `R.getNetUsage() * (1000 / R.getAllFrozenBalanceForBandwidth())`, i.e., roughly half of R's total accumulated usage — even though B's delegation was never consumed — and this usage is credited back to B's own account via `unDelegateIncrease()`.
5. As a result, B receives bandwidth-usage credit it never earned, while A's originally-consumed usage is effectively diluted/misattributed, demonstrating the same "distributed pro-rata across all contributors instead of the specific one" flaw described in the referenced report.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-88)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L103-108)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L317-324)
```java
    //modify AccountStore for receiver
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(balance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(balance);
    }
    accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L115-123)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }
```
