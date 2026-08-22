### Title
Delegate resource lock period is converted to a timestamp using a fixed block-interval constant, causing misalignment between intended block-based lock duration and actual enforced time - ([File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java])

### Summary
`DelegateResourceActuator` accepts a `lockPeriod` expressed in units of blocks and converts it into an absolute expiration timestamp by multiplying it by the compile-time constant `BLOCK_PRODUCED_INTERVAL`, instead of tracking the actual number of blocks produced. This mirrors the reported bug class: a block-count-based value is pre-estimated into a future timestamp using an assumed fixed block time, rather than being resolved against real block-number progress.

### Finding Description
When a user delegates resources with `lock = true`, the actuator computes `lockPeriod` in block units — defaulting to `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL` or a caller-supplied value bounded by `MAX_DELEGATE_LOCK_PERIOD` (itself expressed and validated in block-count units, capped by `ONE_YEAR_BLOCK_NUMBERS`) [1](#0-0) . This block count is then converted into an absolute wall-clock expiration time via `expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL` [2](#0-1) , and this timestamp is what is actually persisted and enforced (via `DelegatedResourceCapsule` expire fields, checked against `now` in `UnDelegateResourceActuator` and `DelegatedResourceStore.unLockExpireResource`) [3](#0-2) [4](#0-3) .

The maximum allowed lock period is validated purely in block-count units against `ONE_YEAR_BLOCK_NUMBERS` [5](#0-4) , while the actually-enforced unlock condition is a timestamp derived by multiplying that same block count by the constant `BLOCK_PRODUCED_INTERVAL`. This is precisely the pattern flagged in the external report: a duration that is conceptually "N blocks" is pre-converted to a future point in time using an assumed fixed block-production interval, rather than being resolved by comparing actual block numbers (e.g. `block.number` vs a stored `lastRewardBlock`-style checkpoint). If the effective block production cadence ever diverges from the hardcoded `BLOCK_PRODUCED_INTERVAL` constant — for example due to missed/skipped DPoS slots, a different production interval on a non-mainnet deployment, or any future change to block timing — the number of blocks actually elapsed by the time `now >= expireTime` will not match the `lockPeriod` block count the user and the `MAX_DELEGATE_LOCK_PERIOD` governance parameter intended to enforce.

### Impact Explanation
A mismatch between the intended block-based lock duration and the time-based enforcement can cause delegated/locked resources to unlock earlier or later than the governance-configured `MAX_DELEGATE_LOCK_PERIOD` intends. This directly affects resource/accounting correctness: an owner could reclaim (`UnDelegateResourceActuator`) and re-freeze or re-delegate locked BANDWIDTH/ENERGY resources sooner than the network's resource-locking policy intends, or conversely have resources locked longer than represented, both of which are resource-accounting integrity issues reachable from a normal broadcast transaction (`DelegateResourceContract`/`UnDelegateResourceContract`).

### Likelihood Explanation
Exploitability depends on the actual block production cadence diverging from the hardcoded `BLOCK_PRODUCED_INTERVAL` constant used throughout `DelegateResourceActuator`, `Wallet`, `DposSlot`, etc. Under normal mainnet operation with consistent 3-second block production this divergence is minimal, so likelihood is low-to-medium; it becomes more significant on networks with different or non-uniform block intervals, or in periods of slot skips, where the assumption "N blocks == N * BLOCK_PRODUCED_INTERVAL milliseconds" no longer holds precisely.

### Recommendation
Track lock expiration using actual block numbers rather than a derived timestamp — e.g., store an `expireBlockNumber` (current `dynamicStore.getLatestBlockHeaderNumber() + lockPeriod`) and compare against the real chain block number at unlock time instead of computing/comparing an estimated timestamp based on the fixed `BLOCK_PRODUCED_INTERVAL` constant, consistent with the report's recommendation of using an actual block-number checkpoint (`lastRewardBlock`-style) rather than an estimated future block/time value.

### Proof of Concept
Not applicable/provided — no runnable PoC was constructed; the analysis is based on static code review of `DelegateResourceActuator.delegateResource` and its expiration-check consumers. [6](#0-5)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L251-259)
```java
  private long getLockPeriod(boolean supportMaxDelegateLockPeriod,
      DelegateResourceContract delegateResourceContract) {
    long lockPeriod = delegateResourceContract.getLockPeriod();
    if (supportMaxDelegateLockPeriod) {
      return lockPeriod == 0 ? DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL : lockPeriod;
    } else {
      return DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L282-298)
```java
  private void delegateResource(byte[] ownerAddress, byte[] receiverAddress, boolean isBandwidth,
                                long balance, boolean lock, long lockPeriod) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicPropertiesStore = chainBaseManager.getDynamicPropertiesStore();
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    DelegatedResourceAccountIndexStore delegatedResourceAccountIndexStore = chainBaseManager
        .getDelegatedResourceAccountIndexStore();

    // 1. unlock the expired delegate resource
    long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, now);

    //modify DelegatedResourceStore
    long expireTime = 0;
    if (lock) {
      expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL;
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java (L37-47)
```java
  public void unLockExpireResource(byte[] from, byte[] to, long now) {
    byte[] lockKey = DelegatedResourceCapsule
        .createDbKeyV2(from, to, true);
    DelegatedResourceCapsule lockResource = get(lockKey);
    if (lockResource == null) {
      return;
    }
    if (lockResource.getExpireTimeForEnergy() >= now
        && lockResource.getExpireTimeForBandwidth() >= now) {
      return;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L270-298)
```java
      case BANDWIDTH: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForBandwidth() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException(
              "insufficient delegatedFrozenBalance(BANDWIDTH), request="
                  + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
      break;
      case ENERGY: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForEnergy() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException("insufficient delegateFrozenBalance(Energy), request="
              + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L717-728)
```java
      case MAX_DELEGATE_LOCK_PERIOD: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_7_2)) {
          throw new ContractValidateException(
              "Bad chain parameter id [MAX_DELEGATE_LOCK_PERIOD]");
        }
        long maxDelegateLockPeriod = dynamicPropertiesStore.getMaxDelegateLockPeriod();
        if (value <= maxDelegateLockPeriod || value > ONE_YEAR_BLOCK_NUMBERS) {
          throw new ContractValidateException(
              "This value[MAX_DELEGATE_LOCK_PERIOD] is only allowed to be greater than "
                  + maxDelegateLockPeriod + " and less than or equal to " + ONE_YEAR_BLOCK_NUMBERS
                      + " !");
        }
```
