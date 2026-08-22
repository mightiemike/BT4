### Title
UnfreezeBalanceV2Actuator never reduces the account's `FreezeV2.amount`, allowing repeated unfreeze calls to mint balance without a corresponding freeze - (File: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java`)

### Summary
The reported bug class is: a search-over-a-list routine (here, a `for`-loop over frozen resource entries looking for a matching type) exists to *update state after finding the item*, but the calling code never actually invokes it, so the state is never mutated even though the transaction is reported as successful. In `UnfreezeBalanceV2Actuator`, the method `updateAccountFrozenInfo(ResourceCode, AccountCapsule, long)` performs exactly this "find-and-decrement" loop over `FreezeV2` list entries, but it is **dead code** — it is never called from `execute()`.

### Finding Description
`execute()` in `UnfreezeBalanceV2Actuator` performs the unfreeze workflow: it computes `unfreezeExpire`, appends a new `UnFreezeV2` entry to the account's pending-withdrawal list via `accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime)`, adjusts the *global* total resource weight via `updateTotalResourceWeight`, and adjusts votes, then persists the account and marks the transaction `SUCESS`: [1](#0-0) 

Nowhere in this method (nor anywhere else in the class) is `updateAccountFrozenInfo` called, even though it exists specifically to decrement the matching `FreezeV2.amount` entry that backs the frozen resource: [2](#0-1) 

Because that per-account `FreezeV2.amount` field is never reduced, the only gate preventing repeated over-unfreeze is `checkUnfreezeBalance()` in `validate()`, which re-reads the *unchanged* `FreezeV2.amount` on every call: [3](#0-2) 

The only remaining limiter is the unfreeze-count cap (`UNFREEZE_MAX_TIMES = 32`), which bounds how many *times* a user may unfreeze, not how much *total value* they can unfreeze: [4](#0-3) [5](#0-4) 

This is the same bug class as the `BasketFacet.sol` report: a validation/search step (find the matching resource entry) succeeds and the caller proceeds to emit a success result / mutate downstream state (add a new withdrawable `UnFreezeV2` entry, reduce global `TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight`, mark `code.SUCESS`) as though the underlying resource was actually consumed — but the "found → consume" step (`updateAccountFrozenInfo`) is never executed, so the resource is not actually consumed.

Note for comparison, the equivalent native-contract processor for TVM (`UnfreezeBalanceV2Processor`) *does* correctly call the same decrement pattern before proceeding, confirming that `updateAccountFrozenInfo` in the actuator was intended to be called analogously but is simply missing from the call path: [6](#0-5) 

### Impact Explanation
An attacker can freeze a small amount of TRX for a resource (e.g. `BANDWIDTH`), then broadcast `UnfreezeBalanceV2Contract` up to `UNFREEZE_MAX_TIMES` (32) times for that same frozen amount before the resource is actually depleted (since `FreezeV2.amount` never decreases, `checkUnfreezeBalance` keeps passing). Each call:
- Adds a new `UnFreezeV2` entry equal to the (still-valid) frozen amount to the account, redeemable as real TRX balance after `unfreezeDelayDays`.
- Subtracts the unfreeze amount from the network's global `TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight`, corrupting global resource accounting (potentially driving totals inconsistent/negative relative to actual frozen supply).

After the delay period, the attacker withdraws via `WithdrawExpireUnfreezeContract`, receiving up to 32x the TRX they actually froze — this is a direct asset/accounting corruption (balance minted from nothing) reachable purely via ordinary broadcast transactions from any account, no privileged role required.

### Likelihood Explanation
High. The path is reachable by any account via a standard broadcast transaction (`UnfreezeBalanceV2Contract`) with no special permissions, no dependency on other actors, and no race condition — a straightforward sequential freeze → repeated unfreeze → withdraw sequence within normal protocol limits (`UNFREEZE_MAX_TIMES`).

### Recommendation
Call `updateAccountFrozenInfo(freezeType, accountCapsule, unfreezeBalance)` inside `execute()` before (or together with) `updateTotalResourceWeight`, mirroring what `UnfreezeBalanceV2Processor.execute()` does for the TVM path, so that the matching `FreezeV2.amount` entry is actually decremented whenever an unfreeze succeeds. Add a regression test asserting that repeated `UnfreezeBalanceV2Contract` calls beyond the actually-frozen amount fail validation.

### Proof of Concept
1. Attacker freezes `X` TRX for `BANDWIDTH` via `FreezeBalanceV2Contract`, creating one `FreezeV2{type=BANDWIDTH, amount=X}` entry.
2. Attacker broadcasts `UnfreezeBalanceV2Contract{resource=BANDWIDTH, unfreeze_balance=X}` repeatedly (up to 32 times, per `UNFREEZE_MAX_TIMES`). Each call:
   - passes `checkUnfreezeBalance` because `FreezeV2.amount` is still `X` (never decremented, per lines 236-248/`execute()` never calling it).
   - appends a new `UnFreezeV2{amount=X, expireTime=now+delay}` entry (line 86).
3. After `unfreezeDelayDays` elapses, attacker calls `WithdrawExpireUnfreezeContract` once; all accumulated `UnFreezeV2` entries (up to `32 * X`) are paid out to the attacker's balance.
4. Attacker ends up with `32 * X` TRX withdrawn while only ever having frozen `X` TRX — confirming the accounting corruption.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L43-44)
```java
  @Getter
  private static final int UNFREEZE_MAX_TIMES = 32;
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L83-99)
```java
    ResourceCode freezeType = unfreezeBalanceV2Contract.getResource();

    long expireTime = this.calcUnfreezeExpireTime(now);
    accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime);

    this.updateTotalResourceWeight(accountCapsule, unfreezeBalanceV2Contract, unfreezeBalance);
    this.updateVote(accountCapsule, unfreezeBalanceV2Contract, ownerAddress);

    if (dynamicStore.supportAllowNewResourceModel()
        && !accountCapsule.oldTronPowerIsInvalid()) {
      accountCapsule.invalidateOldTronPower();
    }

    accountStore.put(ownerAddress, accountCapsule);

    ret.setWithdrawExpireAmount(unfreezeAmount);
    ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L179-182)
```java
    int unfreezingCount = accountCapsule.getUnfreezingV2Count(now);
    if (UNFREEZE_MAX_TIMES <= unfreezingCount) {
      throw new ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L207-227)
```java
  public boolean checkUnfreezeBalance(AccountCapsule accountCapsule,
                                      final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract,
                                      ResourceCode freezeType) {
    boolean checkOk = false;

    long frozenAmount = 0L;
    List<FreezeV2> freezeV2List = accountCapsule.getFrozenV2List();
    for (FreezeV2 freezeV2 : freezeV2List) {
      if (freezeV2.getType().equals(freezeType)) {
        frozenAmount = freezeV2.getAmount();
        break;
      }
    }

    if (unfreezeBalanceV2Contract.getUnfreezeBalance() > 0
        && unfreezeBalanceV2Contract.getUnfreezeBalance() <= frozenAmount) {
      checkOk = true;
    }

    return checkOk;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L236-248)
```java
  public void updateAccountFrozenInfo(ResourceCode freezeType, AccountCapsule accountCapsule, long unfreezeBalance) {
    List<FreezeV2> freezeV2List = accountCapsule.getFrozenV2List();
    for (int i = 0; i < freezeV2List.size(); i++) {
      if (freezeV2List.get(i).getType().equals(freezeType)) {
        FreezeV2 freezeV2 = FreezeV2.newBuilder()
            .setAmount(freezeV2List.get(i).getAmount() - unfreezeBalance)
            .setType(freezeV2List.get(i).getType())
            .build();
        accountCapsule.updateFrozenV2List(i, freezeV2);
        break;
      }
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L118-146)
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

    this.updateTotalResourceWeight(accountCapsule, param.getResourceType(), unfreezeBalance, repo);
    this.updateVote(accountCapsule, param.getResourceType(), ownerAddress, repo);

    if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()
        && !accountCapsule.oldTronPowerIsInvalid()) {
      accountCapsule.invalidateOldTronPower();
    }

    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
    return unfreezeExpireBalance;
  }
```
