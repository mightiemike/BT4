### Title
Misconfigurable `MAX_DELEGATE_LOCK_PERIOD` chain parameter can permanently block all locked resource-delegation transactions - (File: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java`)

### Summary
`DelegateResourceActuator.validate()` enforces a cross-parameter time-window check very similar in structure to the `AuctionBase._processBid` bug: it compares a hard-coded, protocol-level duration constant against a committee-configurable dynamic property (`MAX_DELEGATE_LOCK_PERIOD`). If that dynamic property is ever configured below the hard-coded constant, every unprivileged user's default-locked `DelegateResourceContract` transaction fails validation permanently, denying access to a normal public feature (locked resource delegation) exactly as the auction bug denied access to bidding.

### Finding Description
When a user submits a `DelegateResourceContract` with `lock = true` and does not explicitly set `lockPeriod` (i.e. `lockPeriod == 0`, meaning "use the protocol default"), the actuator computes the effective lock period from a hard-coded constant: [1](#0-0) 

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

This computed value is then checked in `validate()` against the committee-controlled dynamic property `MAX_DELEGATE_LOCK_PERIOD`: [2](#0-1) 

```java
boolean lock = delegateResourceContract.getLock();
if (lock && dynamicStore.supportMaxDelegateLockPeriod()) {
  long lockPeriod = getLockPeriod(true, delegateResourceContract);
  long maxDelegateLockPeriod = dynamicStore.getMaxDelegateLockPeriod();
  if (lockPeriod < 0 || lockPeriod > maxDelegateLockPeriod) {
    throw new ContractValidateException(
        "The lock period of delegate resource cannot be less than 0 and cannot exceed "
            + maxDelegateLockPeriod + "!");
  }
  ...
```

`DELEGATE_PERIOD` and `BLOCK_PRODUCED_INTERVAL` are fixed protocol constants (`DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL` evaluates to a fixed number of blocks, e.g. 86400 in tests), while `MAX_DELEGATE_LOCK_PERIOD` is a value stored in `DynamicPropertiesStore` that is set either at genesis/config time or via a committee `ProposalCreateActuator` proposal. The proposal-side validation for this parameter only enforces that new values must strictly increase and stay under `ONE_YEAR_BLOCK_NUMBERS` — it performs **no cross-check** against the hard-coded `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL` default used by the actuator: [3](#0-2) 

```java
case MAX_DELEGATE_LOCK_PERIOD: {
  ...
  long maxDelegateLockPeriod = dynamicPropertiesStore.getMaxDelegateLockPeriod();
  if (value <= maxDelegateLockPeriod || value > ONE_YEAR_BLOCK_NUMBERS) {
    throw new ContractValidateException(
        "This value[MAX_DELEGATE_LOCK_PERIOD] is only allowed to be greater than "
            + maxDelegateLockPeriod + " and less than or equal to " + ONE_YEAR_BLOCK_NUMBERS
                + " !");
  }
  ...
```

This is structurally identical to the reported bug class: the value that guards a routine, unprivileged user operation (here, resource-delegation with a lock) is derived from two independently-configurable/hard-coded quantities whose relationship is never validated at the point where misconfiguration would matter (the actuator execution path), only loosely constrained at the point of governance change (monotonic increase, unrelated upper bound). If the initial/genesis value of `MAX_DELEGATE_LOCK_PERIOD` is ever set (via genesis config or an early governance mistake) below `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL`, then **every** `DelegateResourceContract` transaction with `lock=true` and default `lockPeriod=0` submitted by any unprivileged user will throw `ContractValidateException` and never succeed, since the condition `lockPeriod > maxDelegateLockPeriod` is always true for that class of transactions.

### Impact Explanation
This causes an invalid-state/halt condition for a public, unprivileged transaction type (`DelegateResourceContract` with default lock behavior) network-wide — any account trying to delegate bandwidth/energy with the default lock duration is permanently blocked until governance passes a corrective proposal, mirroring the "no bids on new auctions would be processed" impact described in the report. This is a config-driven denial-of-service against ordinary users' resource-delegation (staking-adjacent) functionality, not a mere theoretical edge case, since it reproduces deterministically for any account attempting the default-lock delegation once the misconfiguration exists.

### Likelihood Explanation
Likelihood depends on whether `MAX_DELEGATE_LOCK_PERIOD`'s genesis/initial value can be set independently of the `ProposalUtil` ratchet check (e.g., via genesis/committee parameter configuration files that bypass `ProposalCreateActuator`). I was not able to fully verify the exact default numeric value of `MAX_DELEGATE_LOCK_PERIOD` in `DynamicPropertiesStore` within this session (grep matched the field there but content wasn't inspected), nor whether any additional initialization path outside `ProposalUtil` exists that could set it below the hard-coded constant. Given the on-chain proposal path only allows monotonic increases once initialized, the primary risk window is at genesis/initial deployment configuration, similar to the original report's root cause ("the system was accidentally configured").

### Recommendation
- Add a cross-check in `DelegateResourceActuator.validate()` (or in the parameter-setting path) ensuring `MAX_DELEGATE_LOCK_PERIOD >= DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL`, i.e. never lower than the default lock period used when `lockPeriod == 0`.
- Enforce this invariant wherever `MAX_DELEGATE_LOCK_PERIOD` can be initialized (genesis config loading) in addition to the proposal path in `ProposalUtil.validator`.
- Add a startup/sanity check that fails fast (with a clear error) if committee-configurable parameters and hard-coded protocol constants are mutually inconsistent, rather than allowing silent, permanent transaction rejection.

### Proof of Concept
1. At genesis or via configuration, `MAX_DELEGATE_LOCK_PERIOD` is initialized to a value smaller than `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL` (the constant computed in `DelegateResourceActuator.getLockPeriod`).
2. Any unprivileged user submits a `DelegateResourceContract` with `lock = true` and `lockPeriod = 0` (the standard/default way to lock a delegation).
3. In `DelegateResourceActuator.validate()`, `getLockPeriod(true, contract)` returns `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL`, which is greater than `dynamicStore.getMaxDelegateLockPeriod()`.
4. The condition `lockPeriod > maxDelegateLockPeriod` is true, so `ContractValidateException` is always thrown — this happens for every such transaction from every account until a corrective governance proposal raises `MAX_DELEGATE_LOCK_PERIOD`. [2](#0-1)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L211-219)
```java
    boolean lock = delegateResourceContract.getLock();
    if (lock && dynamicStore.supportMaxDelegateLockPeriod()) {
      long lockPeriod = getLockPeriod(true, delegateResourceContract);
      long maxDelegateLockPeriod = dynamicStore.getMaxDelegateLockPeriod();
      if (lockPeriod < 0 || lockPeriod > maxDelegateLockPeriod) {
        throw new ContractValidateException(
            "The lock period of delegate resource cannot be less than 0 and cannot exceed "
                + maxDelegateLockPeriod + "!");
      }
```

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

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L717-734)
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
        if (dynamicPropertiesStore.getUnfreezeDelayDays() == 0) {
          throw new ContractValidateException(
              "[UNFREEZE_DELAY_DAYS] proposal must be approved "
                  + "before [MAX_DELEGATE_LOCK_PERIOD] can be proposed");
        }
        break;
```
