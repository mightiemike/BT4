## Title
Stale absolute `unfreezeExpireTime` desynchronizes from `UNFREEZE_DELAY_DAYS` changes, blocking/altering user withdrawal timing after governance parameter update - (File: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java`, `actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java`)

### Summary
`UnfreezeBalanceV2Actuator` and its TVM counterpart `UnfreezeBalanceV2Processor` compute and persist an **absolute** unfreeze expiration timestamp (`now + unfreezeDelayDays * FROZEN_PERIOD`) at the moment a user unfreezes resources, exactly mirroring the reported `StakedlvlUSD` pattern of storing `cooldownEnd` instead of a relative `cooldownStart`. Once `UNFREEZE_DELAY_DAYS` is later changed via committee proposal, previously queued unfreeze entries keep their stale, pre-change expiration time, causing a divergence between users who unfroze before vs. after the parameter change.

### Finding Description
When a user calls `unfreezeBalanceV2` (or the TVM native contract equivalent), the actuator computes: [1](#0-0) 

and stores this **absolute** timestamp directly on the account's `UnfrozenV2` list entry: [2](#0-1) 

The same pattern exists in the native TVM processor used when unfreezing is triggered from a smart contract: [3](#0-2) [4](#0-3) 

`unfreezeDelayDays` is a governance-controlled dynamic parameter, changeable at any time by a passed committee proposal: [5](#0-4) 

with an allowed range of `[1, 365]` days enforced only at proposal-validation time, with no lower bound of zero required beyond that: [6](#0-5) 

Because the expiry is baked in as an absolute timestamp derived from the delay value **at the time of unfreeze**, any subsequent governance change to `UNFREEZE_DELAY_DAYS` does not retroactively apply to already-pending `UnFreezeV2` entries. Withdrawal eligibility for these entries is checked purely against the stale, previously computed `unfreezeExpireTime` in `WithdrawExpireUnfreezeActuator`/`WithdrawExpireUnfreezeProcessor`: [7](#0-6) 

This is structurally identical to the reported `StakedlvlUSD` root cause: the protocol stores a computed end time rather than a start time + a reference to the current duration parameter, so a change to the duration parameter after the fact cannot retroactively affect already-initiated withdrawal requests.

### Impact Explanation
If the committee reduces `UNFREEZE_DELAY_DAYS` (e.g., in an emergency to shorten the unlock period across the network, analogous to the report's "protocol needs to allow immediate unstaking" scenario), users who called `unfreezeBalanceV2`/`UnfreezeBalanceV2` before the proposal took effect remain locked until their original (longer) stale expiry timestamp, while users who unfreeze after the change benefit from the new, shorter delay. This creates unfair/divergent treatment of otherwise-identical unprivileged users' fund-withdrawal timing purely based on transaction ordering relative to a governance parameter change — the same "some users can immediately access funds while others with an already-initiated request cannot" outcome described in the original report. This affects normal TRX holders' bandwidth/energy/TRON Power unfreeze withdrawals, a widely used and core accounting flow.

### Likelihood Explanation
`UNFREEZE_DELAY_DAYS` is a legitimate, documented on-chain governable parameter (proposal type `#70`) that the committee can and does change over the life of the chain (it defaults to 0/disabled and is enabled/adjusted via proposal). Any adjustment to this parameter after go-live will trigger the divergence for all outstanding `UnFreezeV2` entries, making this a straightforward, non-exotic occurrence rather than a contrived edge case.

### Recommendation
Store the unfreeze **start time** (or persist the `unfreezeDelayDays` value used) with each `UnFreezeV2` entry instead of (or in addition to) the pre-computed absolute `unfreezeExpireTime`, and recompute the effective expiry using the *current* `UNFREEZE_DELAY_DAYS` value at withdrawal-check time (in `unfreezeExpire`/`WithdrawExpireUnfreezeActuator`/`WithdrawExpireUnfreezeProcessor`), or explicitly document/accept that changes to `UNFREEZE_DELAY_DAYS` are prospective-only and do not retroactively adjust in-flight unfreeze requests, and provide a migration/backfill path for pending entries when the parameter changes.

### Proof of Concept
1. Committee passes a proposal setting `UNFREEZE_DELAY_DAYS = 30`.
2. User A calls `unfreezeBalanceV2` for `BANDWIDTH`; `calcUnfreezeExpireTime` stores `expireTime = now + 30 * FROZEN_PERIOD` in `UnfrozenV2` list (`UnfreezeBalanceV2Actuator.java:85-86`).
3. Committee passes a new proposal reducing `UNFREEZE_DELAY_DAYS = 1` (`ProposalService.java:303-317`), intended to let all users withdraw sooner.
4. User B calls `unfreezeBalanceV2` after the change; their entry gets `expireTime = now + 1 * FROZEN_PERIOD`.
5. When `now` reaches User B's 1-day expiry, User B can successfully call `WithdrawExpireUnfreeze` and receive funds (`WithdrawExpireUnfreezeActuator.java:122-136`).
6. User A, despite the governance intent to shorten unlock times, must still wait the full original 30 days because their stored `unfreezeExpireTime` was computed with the old delay and is never recalculated — reproducing the exact "some users can unstake immediately, others with a pending request cannot" divergence from the original report.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L83-86)
```java
    ResourceCode freezeType = unfreezeBalanceV2Contract.getResource();

    long expireTime = this.calcUnfreezeExpireTime(now);
    accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L229-234)
```java
  public long calcUnfreezeExpireTime(long now) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    long unfreezeDelayDays = dynamicStore.getUnfreezeDelayDays();

    return now + unfreezeDelayDays * FROZEN_PERIOD;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L118-134)
```java
  public long execute(UnfreezeBalanceV2Param param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    long unfreezeBalance = param.getUnfreezeBalance();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();

    long unfreezeExpireBalance = this.unfreezeExpire(accountCapsule, now);

    if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    long expireTime = this.calcUnfreezeExpireTime(now, repo);
    accountCapsule.addUnfrozenV2List(param.getResourceType(), unfreezeBalance, expireTime);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L172-176)
```java
  private long calcUnfreezeExpireTime(long now, Repository repo) {
    long unfreezeDelayDays = repo.getDynamicPropertiesStore().getUnfreezeDelayDays();

    return now + unfreezeDelayDays * FROZEN_PERIOD;
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

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L609-618)
```java
      case UNFREEZE_DELAY_DAYS: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_7)) {
          throw new ContractValidateException(
                  "Bad chain parameter id [UNFREEZE_DELAY_DAYS]");
        }
        if (value < 1 || value > 365) {
          throw new ContractValidateException(
                  "This value[UNFREEZE_DELAY_DAYS] is only allowed to be in the range 1-365");
        }
        break;
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
