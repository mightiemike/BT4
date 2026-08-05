### Title
Committee-controlled `MAX_DELEGATE_LOCK_PERIOD` change takes effect immediately and can retroactively invalidate already-broadcast `DelegateResourceContract` transactions - (File: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java`)

### Summary
`DelegateResourceActuator.validate()` checks a user-supplied `lockPeriod` against the current on-chain value of `MAX_DELEGATE_LOCK_PERIOD` at the moment the transaction executes, not at the moment the user constructed/signed it. Because `MAX_DELEGATE_LOCK_PERIOD` is a committee proposal parameter that is applied immediately upon approval (no timelock, no grace period), a user who built a valid delegate transaction under the old limit can have it unexpectedly rejected if the committee changes the parameter before the transaction is packed into a block — the same root cause as the Y2K Finance `timewindow`/`epochHasNotStarted` bug, where an admin-controlled time-based limit changes retroactively and invalidates a transaction that was valid when the user submitted it.

### Finding Description
`ProposalService.process()` handles the `MAX_DELEGATE_LOCK_PERIOD` proposal type and calls `dynamicStore.saveMaxDelegateLockPeriod(entry.getValue())` unconditionally as soon as a proposal is approved by witnesses, with no timelock or delayed activation: [1](#0-0) 

`ProposalUtil.validator()` only bounds the new value against the *current* `maxDelegateLockPeriod`, it does not prevent a decrease that could invalidate in-flight user transactions: [2](#0-1) 

`DelegateResourceActuator.validate()` reads the current `MAX_DELEGATE_LOCK_PERIOD` at validation time (which happens when the block containing the transaction is processed, not when the user signed it) and reverts the transaction if the user-chosen `lockPeriod` now exceeds the (possibly just-lowered) limit: [3](#0-2) 

This mirrors the Y2K Finance bug class precisely: in that report, `Vault.epochHasNotStarted` checks `block.timestamp <= idEpochBegin[id] - timewindow`, and an admin call to `VaultFactory.changeTimewindow` that takes effect immediately can flip an already-valid pending `deposit` call into a revert. Here, `MAX_DELEGATE_LOCK_PERIOD` plays the role of `timewindow`: a value that a normal user reads and relies on to construct a valid transaction, which the governance/committee mechanism can change with immediate effect, invalidating the user's in-flight transaction.

### Impact Explanation
A user who queries the current `MAX_DELEGATE_LOCK_PERIOD` via `getchainparameters` and constructs a `DelegateResourceContract` with `lock = true` and a `lockPeriod` at or near that maximum can have their transaction fail validation (`ContractValidateException`) if the committee proposal lowering `MAX_DELEGATE_LOCK_PERIOD` is approved and applied in an earlier or the same block. This causes wasted transaction fees/bandwidth and unexpected on-chain reverts for the end user, analogous to the "wasted gas and user confusion/unfairness" impact described in the original report. The impact is limited to transaction reverts/wasted resources (an availability/UX issue for unprivileged users), not fund loss or accounting corruption, since `validate()` correctly rejects the invalid delegate rather than executing incorrect state changes.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: `MAX_DELEGATE_LOCK_PERIOD` changes require a committee proposal to be approved by a majority of witnesses (a governance action), which is infrequent and typically increases rather than decreases limits. However, because there is no timelock/delay before the new value takes effect (unlike some blockchains that queue parameter changes for the next maintenance cycle) and no explicit guard preventing lowering it below values relied upon by pending transactions, the window for this race exists any time such a proposal is processed while user transactions referencing the old (higher) limit are in the mempool.

### Recommendation
- Apply committee-approved parameter changes like `MAX_DELEGATE_LOCK_PERIOD` (and similar time/period-based limits) at the start of the next maintenance cycle rather than immediately, giving users a predictable grace period, consistent with how many chain parameters are documented to change.
- Alternatively/additionally, validate `lockPeriod` bounds using a value snapshotted from when the transaction's parent block was built or from `expiration`-time semantics, so that a mid-flight parameter change cannot retroactively invalidate a transaction the user considered valid when signing it.
- Document clearly (as already partly done in `docs/configuration.md`) that `committee.*`-controlled values can change block-to-block, and consider rejecting proposals that lower `MAX_DELEGATE_LOCK_PERIOD` without a delay window.

### Proof of Concept
1. Query `getchainparameters` and observe `MAX_DELEGATE_LOCK_PERIOD = N`.
2. User signs and broadcasts a `DelegateResourceContract` with `lock = true`, `lockPeriod = N` (valid at broadcast time per `DelegateResourceActuator.validate` at [4](#0-3) ).
3. Before the user's transaction is packed, a committee proposal lowering `MAX_DELEGATE_LOCK_PERIOD` to `N-1` is approved and processed via `ProposalService.process` (`case MAX_DELEGATE_LOCK_PERIOD`) at [1](#0-0) , taking effect immediately.
4. When the user's transaction is subsequently validated, `lockPeriod (N) > maxDelegateLockPeriod (N-1)` now holds, and `validate()` throws `ContractValidateException`, reverting the previously-valid transaction and consuming the user's bandwidth/fee.

### Citations

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L355-358)
```java
        case MAX_DELEGATE_LOCK_PERIOD: {
          manager.getDynamicPropertiesStore().saveMaxDelegateLockPeriod(entry.getValue());
          break;
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
