### Title
Non-expired pending unfreeze (`UnFreezeV2`) balances are silently destroyed on contract `SUICIDE`, permanently corrupting TRX accounting - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
When a smart contract account that holds pending, not-yet-matured `UnfreezeBalanceV2` withdrawal requests executes `SUICIDE`/`SUICIDE2`, `transferFrozenV2BalanceToInheritor` only forwards the *already-expired* portion of `unfrozenV2List` to the inheritor/black-hole address. The remaining, not-yet-expired entries are then wiped by `clearOwnerFreezeV2` without being credited to anyone, permanently destroying that TRX value. This mirrors the reported bug class: a "hard-coded"/partial accounting of pending withdrawals that becomes stuck and is never reconciled, corrupting the protocol's balance accounting once a particular state transition (default in the original report, self-destruct here) occurs.

### Finding Description
`UnfreezeBalanceV2Actuator`/`UnfreezeBalanceV2Processor` moves frozen TRX into `unfrozenV2List` entries with a future `unfreezeExpireTime`, and decrements `TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight` immediately at request time [1](#0-0) . The TRX itself is not moved out of the account's balance record yet — it stays encoded only as an entry in `unfrozenV2List`, to be released later by `WithdrawExpireUnfreezeActuator`/`unfreezeExpire` once `unfreezeExpireTime` passes [2](#0-1) .

If the owning account is a smart contract that self-destructs before that expiry, `Program.suicide`/`suicide2` calls `transferFrozenV2BalanceToInheritor`: [3](#0-2) 

Only the subset of `unfrozenV2List` whose `unfreezeExpireTime <= nowTimestamp` is summed into `expireUnfrozenBalance` and credited to the inheritor: [4](#0-3) 

Then `clearOwnerFreezeV2` unconditionally clears **all** entries of `unfrozenV2List` — including the non-expired ones that were never transferred anywhere: [5](#0-4) 

This is the direct analog of the external report's root cause: a pending-withdrawal accounting entry ("hard-coded pending withdrawals") that becomes permanently unclaimable because the code path that should reconcile/pay it out is bypassed by another state transition (there: borrower default; here: contract self-destruction), and the cleanup/removal logic (`clearOwnerFreezeV2`, analogous to `removeYieldSource`) discards it without transferring the underlying value.

### Impact Explanation
The non-expired pending unfreeze amount represents real TRX that was frozen and is owed back to the owner once the delay elapses. After `SUICIDE`, the owning account is deleted (`getResult().addDeleteAccount(...)`), so this TRX value is neither returned to the destroyed owner, nor forwarded to the inheritor/black-hole, nor re-added to any resource weight pool. It is permanently and silently removed from total on-chain accounted supply — a chain-level accounting corruption/asset-destruction bug reachable purely through normal contract execution (an unprivileged contract call), with no attacker privilege required. Any contract that (a) freezes TRTX via `freezeBalanceV2`/native contract call, (b) unfreezes some of it (creating a future-dated `UnFreezeV2` entry), and (c) self-destructs before that entry's expiry, will trigger permanent loss of the pending amount.

### Likelihood Explanation
This requires only a contract-controlled sequence of ordinary, unprivileged operations: freeze → unfreeze (creating a pending, non-expired withdrawal request) → `SUICIDE`. No node compromise, no consensus assumption, and no timing race beyond normal `unfreezeDelayDays` is needed; it is fully reachable from a standard broadcast transaction that deploys/calls a contract implementing this sequence.

### Recommendation
In `transferFrozenV2BalanceToInheritor` (and the corresponding non-`suicide2` path), before calling `clearOwnerFreezeV2`, sum and transfer the *non-expired* remainder of `unfrozenV2List` to the inheritor (or black-hole) address as well as the expired portion, so that no TRX value is discarded. Only clear the list after all pending amounts have been credited somewhere.

### Proof of Concept
Not executable in this read-only analysis; the flow is traceable statically:
1. Deploy contract, freeze TRX via `freezeBalanceV2` for `BANDWIDTH`.
2. Call `unfreezeBalanceV2` for part of the frozen balance — creates an `UnFreezeV2` entry with `unfreezeExpireTime = now + unfreezeDelayDays*FROZEN_PERIOD` (`UnfreezeBalanceV2Processor.execute`, lines 118-146).
3. Immediately call `SUICIDE`/`SUICIDE2` from the contract before that expire time.
4. `transferFrozenV2BalanceToInheritor` only forwards the (zero, since none expired yet) `expireUnfrozenBalance`; `clearOwnerFreezeV2` clears `unfrozenV2List`, discarding the pending amount with no credit to any account — confirmed by reading `Program.java` lines 620-695 above.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L148-170)
```java
  private long unfreezeExpire(AccountCapsule accountCapsule, long now) {
    long unfreezeBalance = 0L;

    List<Protocol.Account.UnFreezeV2> unFrozenV2List = Lists.newArrayList();
    unFrozenV2List.addAll(accountCapsule.getUnfrozenV2List());
    Iterator<Protocol.Account.UnFreezeV2> iterator = unFrozenV2List.iterator();

    while (iterator.hasNext()) {
      Protocol.Account.UnFreezeV2 next = iterator.next();
      if (next.getUnfreezeExpireTime() <= now) {
        unfreezeBalance += next.getUnfreezeAmount();
        iterator.remove();
      }
    }

    accountCapsule.setInstance(
        accountCapsule.getInstance().toBuilder()
            .setBalance(accountCapsule.getBalance() + unfreezeBalance)
            .clearUnfrozenV2()
            .addAllUnfrozenV2(unFrozenV2List).build()
    );
    return unfreezeBalance;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L620-681)
```java
  private long transferFrozenV2BalanceToInheritor(byte[] ownerAddr, byte[] inheritorAddr, Repository repo) {
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddr);
    AccountCapsule inheritorCapsule = repo.getAccount(inheritorAddr);
    long now = repo.getHeadSlot();

    // transfer frozen resource
    ownerCapsule.getFrozenV2List().stream()
        .filter(freezeV2 -> freezeV2.getAmount() > 0)
        .forEach(
            freezeV2 -> {
              switch (freezeV2.getType()) {
                case BANDWIDTH:
                  inheritorCapsule.addFrozenBalanceForBandwidthV2(freezeV2.getAmount());
                  break;
                case ENERGY:
                  inheritorCapsule.addFrozenBalanceForEnergyV2(freezeV2.getAmount());
                  break;
                case TRON_POWER:
                  inheritorCapsule.addFrozenForTronPowerV2(freezeV2.getAmount());
                  break;
              }
            });

    // merge usage
    BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(ChainBaseManager.getInstance());
    bandwidthProcessor.updateUsageForDelegated(ownerCapsule);
    ownerCapsule.setLatestConsumeTime(now);
    if (ownerCapsule.getNetUsage() > 0) {
      bandwidthProcessor.unDelegateIncrease(inheritorCapsule, ownerCapsule,
          ownerCapsule.getNetUsage(), BANDWIDTH, now);
    }

    EnergyProcessor energyProcessor =
        new EnergyProcessor(
            repo.getDynamicPropertiesStore(), ChainBaseManager.getInstance().getAccountStore());
    energyProcessor.updateUsage(ownerCapsule);
    ownerCapsule.setLatestConsumeTimeForEnergy(now);
    if (ownerCapsule.getEnergyUsage() > 0) {
      energyProcessor.unDelegateIncrease(inheritorCapsule, ownerCapsule,
          ownerCapsule.getEnergyUsage(), ENERGY, now);
    }

    // withdraw expire unfrozen balance
    long nowTimestamp = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    long expireUnfrozenBalance =
        ownerCapsule.getUnfrozenV2List().stream()
            .filter(
                unFreezeV2 ->
                    unFreezeV2.getUnfreezeAmount() > 0 && unFreezeV2.getUnfreezeExpireTime() <= nowTimestamp)
            .mapToLong(Protocol.Account.UnFreezeV2::getUnfreezeAmount)
            .sum();
    if (expireUnfrozenBalance > 0) {
      inheritorCapsule.setBalance(inheritorCapsule.getBalance() + expireUnfrozenBalance);
      increaseNonce();
      addInternalTx(null, ownerAddr, inheritorAddr, expireUnfrozenBalance, null,
          "withdrawExpireUnfreezeWhileSuiciding", nonce, null);
    }
    clearOwnerFreezeV2(ownerCapsule);
    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
    repo.updateAccount(inheritorCapsule.createDbKey(), inheritorCapsule);
    return expireUnfrozenBalance;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L688-695)
```java
  private void clearOwnerFreezeV2(AccountCapsule ownerCapsule) {
    ownerCapsule.clearFrozenV2();
    ownerCapsule.setNetUsage(0);
    ownerCapsule.setNewWindowSize(BANDWIDTH, 0);
    ownerCapsule.setEnergyUsage(0);
    ownerCapsule.setNewWindowSize(ENERGY, 0);
    ownerCapsule.clearUnfrozenV2();
  }
```
