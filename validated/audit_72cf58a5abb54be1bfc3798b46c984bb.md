### Title
Governance reduction of `UNFREEZE_DELAY_DAYS` cannot free already-queued unfreezes because the withdrawal time is baked in as an absolute timestamp - ([File: actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java])

### Summary
The reported StakedlvlUSD bug is caused by persisting an *absolute* cooldown-end timestamp at the moment a user initiates cooldown, instead of persisting the cooldown *start* time plus a reference to the (mutable) cooldown duration. When the duration parameter is later reduced/removed to allow emergency exits, users who already started cooldown remain locked to the stale, longer absolute end time. java-tron's TVM-era freeze/unfreeze accounting (`UnfreezeBalanceV2Actuator`, `UnfreezeBalanceV2Processor`, `WithdrawExpireUnfreezeActuator/Processor`) has the exact same structural pattern: the unfreeze-lock expiry is computed once from the *then-current* `UNFREEZE_DELAY_DAYS` dynamic parameter and stored as an absolute `unfreezeExpireTime` in each `UnFreezeV2` record, never revisited if the parameter changes later.

### Finding Description
When an account calls `unfreezeBalanceV2` (via `UnfreezeBalanceV2Actuator.execute` or the TVM-native `UnfreezeBalanceV2Processor.execute`), the actuator computes the withdrawal-eligible time as: [1](#0-0) 

`calcUnfreezeExpireTime(now)` reads the *current* `unfreezeDelayDays` from `DynamicPropertiesStore` and adds it (in `FROZEN_PERIOD` units) to `now`, producing an absolute timestamp that is written into the account's `UnFreezeV2` list via `accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime)`: [2](#0-1) 

The TVM native-contract path (`UnfreezeBalanceV2Processor`) contains the identical logic: [3](#0-2) 

This absolute `unfreezeExpireTime` is what `WithdrawExpireUnfreezeActuator`/`WithdrawExpireUnfreezeProcessor` and the `CancelAllUnfreezeV2Processor` later compare against `now` to decide whether funds are withdrawable: [4](#0-3) [5](#0-4) 

`UNFREEZE_DELAY_DAYS` is a committee-governed dynamic parameter that can be changed at any time via a proposal, and `ProposalService.process` writes the new value directly into the dynamic-properties store without touching any already-queued `UnFreezeV2` entries: [6](#0-5) 

Because each queued unfreeze entry's `unfreezeExpireTime` was computed and persisted at the moment `unfreezeBalanceV2` executed (using whatever `unfreezeDelayDays` was in effect at that time), reducing (or zeroing) `UNFREEZE_DELAY_DAYS` afterward has no effect on entries that are already in the `unfrozenV2` list — they remain locked until their originally-computed absolute expiry. This is structurally identical to the reported StakedlvlUSD flaw: storing an absolute "end" value derived from a duration parameter at initiation time, rather than storing the start time and re-deriving the end time from the *current* duration parameter at withdrawal time.

### Impact Explanation
If the TRON committee needs to shorten or eliminate the unfreeze delay in an emergency (e.g., to let users exit quickly during an incident), any user who already called `unfreezeBalanceV2`/`UnfreezeBalanceV2Contract` before the parameter change remains locked out of their TRX until the stale, longer expiry time is reached, while users who call `unfreezeBalanceV2` *after* the parameter change get the shorter delay. This produces inconsistent, unintended fund-lock behavior across users and defeats the purpose of an emergency parameter reduction — an availability/DoS-style impact on legitimate withdrawal of already-unfrozen (but pre-committed) balances. It does not permit theft or double-spend, since the accounting itself (amounts) stays correct; the impact is limited to delayed access to funds for a subset of users following a governance parameter change.

### Likelihood Explanation
This requires a governance/committee action to reduce or change `UNFREEZE_DELAY_DAYS` (via `ProposalService`), which is a privileged/administrative operation, combined with users having queued unfreezes beforehand under the old delay. The path from an ordinary user's perspective (calling `unfreezeBalanceV2`) is fully reachable by any account, but the trigger condition (governance changing the parameter) is not attacker-controlled. This is a design/consistency issue exposed by a legitimate emergency governance action rather than an independently exploitable vulnerability by any unprivileged party.

### Recommendation
If the intent of reducing `UNFREEZE_DELAY_DAYS` is to allow faster/emergency withdrawal for all pending unfreezes, consider recomputing (or capping) existing `UnFreezeV2.unfreezeExpireTime` entries when the parameter changes, or storing the unfreeze *start* time plus reading the current `unfreezeDelayDays` at withdrawal-check time (mirroring the external report's suggested fix), so that a reduction in delay retroactively benefits users who are already in the unfreeze queue. Alternatively, document this as intended (non-retroactive) behavior if consistency with already-committed unfreezes is a deliberate design choice.

### Proof of Concept
1. Committee sets `unfreezeDelayDays = 30` (default via `saveUnfreezeDelayDays`).
2. Alice calls `unfreezeBalanceV2`; `UnfreezeBalanceV2Actuator.execute` calls `calcUnfreezeExpireTime(now)` = `now + 30*FROZEN_PERIOD`, stored in her `UnFreezeV2.unfreezeExpireTime`. [2](#0-1) 
3. Committee passes a proposal (`UNFREEZE_DELAY_DAYS` case) reducing `unfreezeDelayDays` to 0 for emergency withdrawals. [6](#0-5) 
4. Bob calls `unfreezeBalanceV2` after the change; his entry gets `expireTime = now` (immediately withdrawable).
5. Alice calls `withdrawExpireUnfreeze`; `WithdrawExpireUnfreezeActuator.validate/execute` filters her entry by `unfreezeExpireTime <= now`, which still fails because her stored `unfreezeExpireTime` reflects the old 30-day delay — she cannot withdraw despite the emergency reduction, while Bob can. [7](#0-6) 

Note: I was unable to fully retrieve the exact bounds/validation logic in `ProposalUtil.java` for the `UNFREEZE_DELAY_DAYS` case (e.g., minimum/maximum allowed values) due to tool-call limits, so I cannot confirm whether the committee is restricted from setting the value to 0 or below the FROZEN_PERIOD granularity; this does not affect the core finding that already-stored `unfreezeExpireTime` values are never recomputed when the parameter changes.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L83-88)
```java
    ResourceCode freezeType = unfreezeBalanceV2Contract.getResource();

    long expireTime = this.calcUnfreezeExpireTime(now);
    accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime);

    this.updateTotalResourceWeight(accountCapsule, unfreezeBalanceV2Contract, unfreezeBalance);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L229-234)
```java
  public long calcUnfreezeExpireTime(long now) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    long unfreezeDelayDays = dynamicStore.getUnfreezeDelayDays();

    return now + unfreezeDelayDays * FROZEN_PERIOD;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L172-176)
```java
  private long calcUnfreezeExpireTime(long now, Repository repo) {
    long unfreezeDelayDays = repo.getDynamicPropertiesStore().getUnfreezeDelayDays();

    return now + unfreezeDelayDays * FROZEN_PERIOD;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L107-112)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<UnFreezeV2> unfrozenV2List = accountCapsule.getInstance().getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze <= 0) {
      throw new ContractValidateException("no unFreeze balance to withdraw ");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L122-136)
```java
  private long getTotalWithdrawUnfreeze(List<UnFreezeV2> unfrozenV2List, long now) {
    return getTotalWithdrawList(unfrozenV2List, now).stream()
        .mapToLong(UnFreezeV2::getUnfreezeAmount).sum();
  }

  private List<UnFreezeV2> getTotalWithdrawList(List<UnFreezeV2> unfrozenV2List, long now) {
    return unfrozenV2List.stream().filter(unfrozenV2 -> unfrozenV2.getUnfreezeExpireTime() <= now)
        .collect(Collectors.toList());
  }

  private List<UnFreezeV2> getRemainWithdrawList(List<UnFreezeV2> unfrozenV2List, long now) {
    return unfrozenV2List.stream()
        .filter(unfrozenV2 -> unfrozenV2.getUnfreezeExpireTime() > now)
        .collect(Collectors.toList());
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L50-59)
```java
    for (Protocol.Account.UnFreezeV2 unFreezeV2: ownerCapsule.getUnfrozenV2List()) {
      if (unFreezeV2.getUnfreezeExpireTime() > now) {
        String resourceName = unFreezeV2.getType().name();
        result.put(resourceName, result.getOrDefault(resourceName, 0L) + unFreezeV2.getUnfreezeAmount());

        updateFrozenInfoAndTotalResourceWeight(ownerCapsule, unFreezeV2, repo);
      } else {
        // withdraw
        withdrawExpireBalance += unFreezeV2.getUnfreezeAmount();
      }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L303-317)
```java
        case UNFREEZE_DELAY_DAYS: {
          DynamicPropertiesStore dynamicStore = manager.getDynamicPropertiesStore();
          dynamicStore.saveUnfreezeDelayDays(entry.getValue());
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.FreezeBalanceV2Contract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.UnfreezeBalanceV2Contract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.WithdrawExpireUnfreezeContract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.DelegateResourceContract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.UnDelegateResourceContract_VALUE);
          break;
        }
```
