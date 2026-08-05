### Title
Incomplete State Restoration in `CancelAllUnfreezeV2Actuator` — Frozen Balance Restored but Votes Not Recomputed - (File: `actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java`)

### Summary
`CancelAllUnfreezeV2Actuator.execute` reverses a pending V2 unfreeze by re-adding the amount back to the user's frozen balance and to the network's total resource weight, but it never recomputes/restores the user's `VotesCapsule`/`AccountCapsule` vote counts that were reduced (or cleared entirely) by the corresponding `UnfreezeBalanceV2Actuator.updateVote` call when the unfreeze was originally queued. This is the same class of bug as the reported `cancelWithdrawal` issue: a cancel operation only rolls back part of the state mutated by the original action, leaving the protocol's internal accounting (here, vote weight vs. actual TRON Power) inconsistent.

### Finding Description
When a user calls `unfreezeBalanceV2` (`UnfreezeBalanceV2Actuator.execute`, lines 74-96), two independent state changes occur:
1. `updateTotalResourceWeight` subtracts the unfrozen amount from the account's frozen balance and from the network total weight. [1](#0-0) 
2. `updateVote` adjusts or entirely clears the account's votes (and the corresponding `VotesCapsule` in `VotesStore`) if the account's remaining TRON Power is now insufficient to support its existing votes, or clears all votes outright once the new resource model is active and old TRON Power becomes invalid. [2](#0-1) 

When the user later cancels that pending unfreeze with `cancelAllUnfreezeV2` before it expires, `CancelAllUnfreezeV2Actuator.execute` only restores the frozen balance and total weight via `updateFrozenInfoAndTotalResourceWeight`: [3](#0-2) [4](#0-3) 

There is no call to any vote-recomputation logic (no `updateVote`, no `VotesStore` write) in `CancelAllUnfreezeV2Actuator`, nor in the equivalent TVM path `CancelAllUnfreezeV2Processor.execute`: [5](#0-4) 

Consequently, after a cancel: the account's TRON Power/frozen balance is fully restored to its pre-unfreeze value, but its `votes` field in `AccountCapsule` and the `VotesStore` entry remain at the reduced (or zero) value that was set when the unfreeze was originally processed. The account's real staking weight and its recorded voting weight diverge — an accounting inconsistency directly analogous to the `hypeBuffer` divergence described in the report (partial state rollback on cancellation).

### Impact Explanation
This produces a state divergence between an account's actual TRON Power (fully restored) and its recorded votes (still reduced), used by `MaintenanceManager` during the maintenance cycle to tally SR votes from `VotesStore`. The user's voting weight is silently understated relative to their real staked balance until they explicitly re-vote, meaning:
- Governance/witness vote tallies are transiently inconsistent with actual stake.
- The user has no visibility that their votes were invalidated by an unfreeze they later reversed, unlike an explicit unvote action.

This matches the report's "Accounting Inconsistency" impact category rather than a direct fund-loss bug — no assets are lost, but internal state (vote weight vs. stake) becomes misaligned until corrected by a subsequent explicit vote transaction, which is exactly the "suboptimal operational/governance decisions" class of impact called out in the original report.

### Likelihood Explanation
This is reachable by any unprivileged account and requires no special permissions: freeze for TRON Power → vote → unfreeze (with the new resource model active) → cancel the unfreeze before expiry via `cancelAllUnfreezeV2`/`cancelAllUnfreezeV2Action`. The scenario is a normal user flow (not a mock or theoretical-only path), and `dbManager.getDynamicPropertiesStore().supportAllowCancelAllUnfreezeV2()` simply needs to be enabled by the committee, which is the intended production configuration for this feature.

### Recommendation
In `CancelAllUnfreezeV2Actuator.execute` (and the analogous `CancelAllUnfreezeV2Processor.execute`), after restoring the frozen balance/resource weight for each still-pending `UnFreezeV2` entry, invoke the same vote-recalculation logic used in `UnfreezeBalanceV2Actuator.updateVote`/`UnfreezeBalanceV2Processor.updateVote` (or a shared helper) to recompute and persist the account's votes and the corresponding `VotesStore` entry based on the restored TRON Power, so that vote weight and frozen balance remain consistent after a cancellation.

### Proof of Concept
1. Freeze for TRON Power: `freezeBalanceV2(amount, TRON_POWER)`.
2. Vote for a witness with the full available TRON Power via `VoteWitnessActuator`.
3. Call `unfreezeBalanceV2(amount, TRON_POWER)` — this triggers `UnfreezeBalanceV2Actuator.updateVote`, clearing/reducing the account's votes (verified by the existing test `UnfreezeBalanceV2ActuatorTest.testVotes`, which asserts votes are reduced/cleared after unfreeze): [6](#0-5) 
4. Before the unfreeze expires, call `cancelAllUnfreezeV2` — the existing test `CancelAllUnfreezeV2ActuatorTest.testCancelAllUnfreezeV2` and `FreezeV2Test.cancelAllUnfreezeV2` only assert that frozen balance is restored (`oldFrozenBalance + oldUnfreezingBalance == newFrozenBalance`), never that votes are restored: [7](#0-6) 
5. Inspect `AccountStore`/`VotesStore` for the account: frozen balance/TRON Power is back to its original value, but `getVotesList()`/`VotesCapsule` still reflects the reduced/cleared vote count set in step 3 — demonstrating the same "restore part of the state but not all of it" inconsistency as the reported `cancelWithdrawal` bug.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L274-301)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract,
                                        long unfreezeBalance) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    switch (unfreezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L303-337)
```java
  private void updateVote(AccountCapsule accountCapsule,
                          final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract,
                          byte[] ownerAddress) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    VotesStore votesStore = chainBaseManager.getVotesStore();

    if (accountCapsule.getVotesList().isEmpty()) {
      return;
    }
    if (dynamicStore.supportAllowNewResourceModel()) {
      if (accountCapsule.oldTronPowerIsInvalid()) {
        switch (unfreezeBalanceV2Contract.getResource()) {
          case BANDWIDTH:
          case ENERGY:
            // there is no need to change votes
            return;
          default:
            break;
        }
      } else {
        // clear all votes at once when new resource model start
        VotesCapsule votesCapsule;
        if (!votesStore.has(ownerAddress)) {
          votesCapsule = new VotesCapsule(
              unfreezeBalanceV2Contract.getOwnerAddress(),
              accountCapsule.getVotesList()
          );
        } else {
          votesCapsule = votesStore.get(ownerAddress);
        }
        accountCapsule.clearVotes();
        votesCapsule.clearNewVotes();
        votesStore.put(ownerAddress, votesCapsule);
        return;
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java (L71-90)
```java
    for (UnFreezeV2 unFreezeV2 : unfrozenV2List) {
      updateAndCalculate(triple, ownerCapsule, now, atomicWithdrawExpireBalance, unFreezeV2);
    }
    ownerCapsule.clearUnfrozenV2();
    addTotalResourceWeight(dynamicStore, triple);

    long withdrawExpireBalance = atomicWithdrawExpireBalance.get();
    if (withdrawExpireBalance > 0) {
      ownerCapsule.setBalance(ownerCapsule.getBalance() + withdrawExpireBalance);
    }

    accountStore.put(ownerCapsule.createDbKey(), ownerCapsule);
    ret.setWithdrawExpireAmount(withdrawExpireBalance);
    Map<String, Long> cancelUnfreezeV2AmountMap = new HashMap<>();
    cancelUnfreezeV2AmountMap.put(BANDWIDTH.name(), triple.getLeft().getRight().get());
    cancelUnfreezeV2AmountMap.put(ENERGY.name(), triple.getMiddle().getRight().get());
    cancelUnfreezeV2AmountMap.put(TRON_POWER.name(), triple.getRight().getRight().get());
    ret.putAllCancelUnfreezeV2AmountMap(cancelUnfreezeV2AmountMap);
    ret.setStatus(fee, code.SUCESS);
    return true;
```

**File:** actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java (L176-205)
```java
  public void updateFrozenInfoAndTotalResourceWeight(
      AccountCapsule accountCapsule, UnFreezeV2 unFreezeV2,
      Triple<Pair<AtomicLong, AtomicLong>, Pair<AtomicLong, AtomicLong>,
          Pair<AtomicLong, AtomicLong>> triple) {
    switch (unFreezeV2.getType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(unFreezeV2.getUnfreezeAmount());
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        triple.getLeft().getLeft().addAndGet(newNetWeight - oldNetWeight);
        triple.getLeft().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(unFreezeV2.getUnfreezeAmount());
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        triple.getMiddle().getLeft().addAndGet(newEnergyWeight - oldEnergyWeight);
        triple.getMiddle().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(unFreezeV2.getUnfreezeAmount());
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        triple.getRight().getLeft().addAndGet(newTPWeight - oldTPWeight);
        triple.getRight().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      default:
        break;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L44-70)
```java
  public Map<String, Long> execute(CancelAllUnfreezeV2Param param, Repository repo) throws ContractExeException {
    Map<String, Long> result = new HashMap<>();
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    long withdrawExpireBalance = 0L;
    for (Protocol.Account.UnFreezeV2 unFreezeV2: ownerCapsule.getUnfrozenV2List()) {
      if (unFreezeV2.getUnfreezeExpireTime() > now) {
        String resourceName = unFreezeV2.getType().name();
        result.put(resourceName, result.getOrDefault(resourceName, 0L) + unFreezeV2.getUnfreezeAmount());

        updateFrozenInfoAndTotalResourceWeight(ownerCapsule, unFreezeV2, repo);
      } else {
        // withdraw
        withdrawExpireBalance += unFreezeV2.getUnfreezeAmount();
      }
    }
    if (withdrawExpireBalance > 0) {
      ownerCapsule.setBalance(ownerCapsule.getBalance() + withdrawExpireBalance);
    }
    ownerCapsule.clearUnfrozenV2();

    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);

    result.put(VMConstant.WITHDRAW_EXPIRE_BALANCE, withdrawExpireBalance);
    return result;
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/UnfreezeBalanceV2ActuatorTest.java (L284-339)
```java
  @Test
  public void testVotes() {
    byte[] ownerAddressBytes = ByteArray.fromHexString(OWNER_ADDRESS);
    long unfreezeBalance = frozenBalance / 2;
    long now = System.currentTimeMillis();
    dbManager.getDynamicPropertiesStore().saveLatestBlockHeaderTimestamp(now);
    dbManager.getDynamicPropertiesStore().saveAllowNewResourceModel(0);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddressBytes);
    accountCapsule.addFrozenBalanceForBandwidthV2(1_000_000_000L);
    accountCapsule.addVotes(ByteString.copyFrom(RECEIVER_ADDRESS.getBytes()), 500);
    accountCapsule.addVotes(ByteString.copyFrom(OWNER_ACCOUNT_INVALID.getBytes()), 500);
    dbManager.getAccountStore().put(accountCapsule.createDbKey(), accountCapsule);

    UnfreezeBalanceV2Actuator actuator = new UnfreezeBalanceV2Actuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContractForBandwidthV2(OWNER_ADDRESS, unfreezeBalance));
    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      VotesCapsule votesCapsule = dbManager.getVotesStore().get(ownerAddressBytes);
      Assert.assertNotNull(votesCapsule);
      for (Vote vote : votesCapsule.getOldVotes()) {
        Assert.assertEquals(vote.getVoteCount(), 500);
      }
      for (Vote vote : votesCapsule.getNewVotes()) {
        Assert.assertEquals(vote.getVoteCount(), 250);
      }
      accountCapsule = dbManager.getAccountStore().get(ownerAddressBytes);
      for (Vote vote : accountCapsule.getVotesList()) {
        Assert.assertEquals(vote.getVoteCount(), 250);
      }
    } catch (ContractValidateException | ContractExeException e) {
      Assert.fail("cannot run here.");
    }

    // clear for new resource model
    dbManager.getDynamicPropertiesStore().saveAllowNewResourceModel(1);
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContractForBandwidthV2(OWNER_ADDRESS, unfreezeBalance / 2));
    try {
      actuator.validate();
      actuator.execute(ret);
      VotesCapsule votesCapsule = dbManager.getVotesStore().get(ownerAddressBytes);
      Assert.assertNotNull(votesCapsule);
      for (Vote vote : votesCapsule.getOldVotes()) {
        Assert.assertEquals(vote.getVoteCount(), 500);
      }
      Assert.assertEquals(0, votesCapsule.getNewVotes().size());
      accountCapsule = dbManager.getAccountStore().get(ownerAddressBytes);
      Assert.assertEquals(0, accountCapsule.getVotesList().size());
    } catch (ContractValidateException | ContractExeException e) {
      Assert.fail("cannot run here.");
    }
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeV2Test.java (L727-752)
```java
  private TVMTestResult cancelAllUnfreezeV2(
      byte[] callerAddr, byte[] contractAddr, long expectedWithdrawBalance) throws Exception {
    AccountStore accountStore = dbManager.getAccountStore();
    AccountCapsule oldOwner = accountStore.get(contractAddr);
    long oldBalance = oldOwner.getBalance();
    long now = dbManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    long oldFrozenBalance =
        oldOwner.getFrozenV2List().stream().mapToLong(Protocol.Account.FreezeV2::getAmount).sum();
    long oldUnfreezingBalance =
        oldOwner.getUnfrozenV2List().stream()
            .filter(unFreezeV2 -> unFreezeV2.getUnfreezeExpireTime() > now)
            .mapToLong(Protocol.Account.UnFreezeV2::getUnfreezeAmount)
            .sum();

    TVMTestResult result = triggerCancelAllUnfreezeV2(callerAddr, contractAddr, SUCCESS, null);

    AccountCapsule newOwner = accountStore.get(contractAddr);
    long newUnfreezeV2Amount = newOwner.getUnfreezingV2Count(now);
    long newFrozenBalance =
        newOwner.getFrozenV2List().stream().mapToLong(Protocol.Account.FreezeV2::getAmount).sum();
    Assert.assertEquals(0, newUnfreezeV2Amount);
    Assert.assertEquals(expectedWithdrawBalance, newOwner.getBalance() - oldBalance);
    Assert.assertEquals(oldFrozenBalance + oldUnfreezingBalance, newFrozenBalance);

    return result;
  }
```
