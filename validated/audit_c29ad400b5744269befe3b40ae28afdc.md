### Title
Legacy `UnfreezeBalanceContract` withdraws frozen TRX backing witness votes without reducing the account's cast votes, letting unbacked voting power persist until the voter manually re-votes - ([File: actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java])

### Summary
`UnfreezeBalanceActuator` lets an account reclaim frozen TRX (bandwidth/energy/tron-power freeze) but never touches the account's outstanding `Vote` entries or the `VotesStore`. By contrast, the newer `UnfreezeBalanceV2Actuator` explicitly recalculates and proportionally shrinks the account's votes whenever frozen balance is withdrawn [1](#0-0) . The legacy V1 path has no equivalent logic [2](#0-1) , so votes cast via `VoteWitnessActuator` remain fully counted for a witness even after the voter has withdrawn the underlying stake that justified them - directly analogous to the reported "delegated boost persists after collateral is withdrawn" bug class.

### Finding Description
`VoteWitnessActuator.validate()` checks the voter's `tronPower` only at the moment the vote transaction is submitted [3](#0-2) . Once accepted, `countVoteAccount()` records the vote as a delta (`old`/`new` votes) in `VotesStore` and on the `AccountCapsule` [4](#0-3) . These deltas are later folded permanently into `WitnessCapsule.voteCount` during `MaintenanceManager.doMaintenance()`/`countVote()`, and only change again if a new record appears in `VotesStore` [5](#0-4) .

For the Stake 2.0 unfreeze path (`UnfreezeBalanceV2Actuator`), the developers correctly recognized that withdrawing frozen balance must reduce the voter's outstanding votes, and implemented `updateVote()`, which recomputes `ownedTronPower` and proportionally scales down (or clears) the account's votes and pushes a corrective delta into `VotesStore` [1](#0-0) . This is unit-tested and shown to correctly halve/clear votes when the backing balance is unfrozen [6](#0-5) .

The legacy `UnfreezeBalanceActuator.execute()`, however, only adjusts the account's own frozen-balance fields and the global `TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight` counters used for bandwidth/energy pricing [2](#0-1) . It never imports or references `VotesStore`, never calls `accountCapsule.clearVotes()`, and never writes a corrective `VotesCapsule` delta — confirmed by the absence of any `VotesStore`/`clearVotes` reference in the file. As a result, a user can:
1. Freeze TRX and cast a vote for a witness (`VoteWitnessContract`), which is added to `VotesStore` and eventually folded into `WitnessCapsule.voteCount` at the next maintenance cycle.
2. Call `UnfreezeBalanceContract` to reclaim the TRX (releasing bandwidth/tron-power backing the vote).
3. The witness's `voteCount` is never reduced — the vote entry in `VotesStore`/`AccountCapsule.getVotesList()` is untouched, so it persists at every subsequent maintenance cycle until the user submits a brand-new `VoteWitnessContract` (which re-checks `tronPower` and would fail/shrink), something an attacker is never obligated to do.

This mirrors the reported BoostController pattern exactly: a balance check performed only at "delegation" time (here, vote-casting time), with no re-validation or automatic revocation when the backing collateral is withdrawn through a different, unrelated code path.

### Impact Explanation
Witness vote counts directly determine the active Super Representative set (`updateWitness`) and consensus-level block production/reward eligibility, as well as reward distribution via `incentiveManager.reward()` [7](#0-6) . Unbacked votes persisting indefinitely allow an attacker to cheaply and repeatedly inflate a witness's vote weight (freeze → vote → unfreeze, repeated across many accounts/cycles) without maintaining any real locked stake, distorting SR elections and reward accounting — a consensus/governance integrity issue, not merely a griefing bug.

### Likelihood Explanation
Reachability requires only ordinary, unprivileged broadcast transactions (`FreezeBalanceContract`/`VoteWitnessContract`/`UnfreezeBalanceContract`), all callable by any anonymous account with TRX. The main uncertainty is whether the legacy V1 freeze/unfreeze actuators are still enabled on a given deployment once the Stake 2.0 flag (`supportUnfreezeDelay`) is active; I was not able to fully verify within the available context whether `UnfreezeBalanceActuator.validate()` includes an early rejection when `supportUnfreezeDelay()` is true (I only inspected the DR-delegation branch of `validate()`, not its very first lines). If such a network-wide gate exists and is active, this exact V1 path would be blocked on that specific chain; however, the underlying design flaw (votes not being re-validated against a shrinking stake) is real and demonstrated to require an explicit fix in the parallel V2 code path, confirming the developers themselves recognized and needed to patch this exact defect for V2.

### Recommendation
1. In `UnfreezeBalanceActuator.execute()`, add the same kind of `updateVote()` logic already present in `UnfreezeBalanceV2Actuator` — recompute the account's `tronPower` after unfreeze and proportionally reduce/clear the account's votes and push a corrective delta to `VotesStore`, mirroring `UnfreezeBalanceV2Actuator.updateVote()` [1](#0-0) .
2. As defense in depth, confirm (and if missing, add) an explicit rejection in `UnfreezeBalanceActuator.validate()`/`FreezeBalanceActuator.validate()` once `supportUnfreezeDelay()` is active, so the legacy path can never be exploited in parallel with Stake 2.0's corrected vote-adjustment logic.
3. Consider validating/recomputing `tronPower` vs. cast votes generically at maintenance time (in `MaintenanceManager.countVote()`), independent of which unfreeze actuator was used, so any future code path that reduces frozen balance cannot silently leave stale votes.

### Proof of Concept
1. Account `A` freezes `X` TRX for bandwidth via `FreezeBalanceContract`, giving it `tronPower = X`.
2. `A` calls `VoteWitnessContract` to cast `X` TRX worth of votes for witness `W`; `VoteWitnessActuator.validate()` passes (`sum <= tronPower`), and `countVoteAccount()` records `+X` votes for `W` in `VotesStore`/`A`'s `AccountCapsule` [8](#0-7) .
3. At the next maintenance cycle, `MaintenanceManager.doMaintenance()` folds this `+X` delta into `WitnessCapsule.voteCount` for `W` [9](#0-8) .
4. `A` calls `UnfreezeBalanceContract` for bandwidth, reclaiming the `X` TRX. `UnfreezeBalanceActuator.execute()` reduces `A`'s frozen balance and the global `TotalNetWeight`, but never reduces `A`'s vote entries or writes any correction to `VotesStore` [2](#0-1) .
5. `A` now has `tronPower = 0` but witness `W` still permanently carries the `+X` vote credit from step 3 at every subsequent maintenance cycle, since nothing ever emits a corrective `-X` delta into `VotesStore` unless `A` submits another `VoteWitnessContract`.
6. Repeating steps 1–4 with the same TRX (freeze → vote → unfreeze) lets an attacker accumulate arbitrarily large, unbacked vote weight for a chosen witness at negligible cost, distorting SR elections and reward distribution.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L303-357)
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
    }

    long totalVote = 0;
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      totalVote += vote.getVoteCount();
    }
    long ownedTronPower;
    if (dynamicStore.supportAllowNewResourceModel()) {
      ownedTronPower = accountCapsule.getAllTronPower();
    } else {
      ownedTronPower = accountCapsule.getTronPower();
    }

    // tron power is enough to total votes
    if (ownedTronPower >= totalVote * TRX_PRECISION) {
      return;
    }
    if (totalVote == 0) {
      return;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L192-256)
```java
    } else {
      switch (unfreezeBalanceContract.getResource()) {
        case BANDWIDTH:
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          List<Frozen> frozenList = Lists.newArrayList();
          frozenList.addAll(accountCapsule.getFrozenList());
          Iterator<Frozen> iterator = frozenList.iterator();
          long now = dynamicStore.getLatestBlockHeaderTimestamp();
          while (iterator.hasNext()) {
            Frozen next = iterator.next();
            if (next.getExpireTime() <= now) {
              unfreezeBalance += next.getFrozenBalance();
              iterator.remove();
            }
          }

          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearFrozen().addAllFrozen(frozenList).build());
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          decrease = newNetWeight - oldNetWeight;
          break;
        case ENERGY:
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          unfreezeBalance = accountCapsule.getAccountResource().getFrozenBalanceForEnergy()
              .getFrozenBalance();

          AccountResource newAccountResource = accountCapsule.getAccountResource().toBuilder()
              .clearFrozenBalanceForEnergy().build();
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .setAccountResource(newAccountResource).build());
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          decrease = newEnergyWeight - oldEnergyWeight;
          break;
        case TRON_POWER:
          long oldTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
          unfreezeBalance = accountCapsule.getTronPowerFrozenBalance();
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearTronPower().build());
          long newTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
          decrease = newTPWeight - oldTPWeight;
          break;
        default:
          //this should never happen
          break;
      }

    }
    
    long weight = dynamicStore.allowNewReward() ? decrease : -unfreezeBalance / TRX_PRECISION;
    switch (unfreezeBalanceContract.getResource()) {
      case BANDWIDTH:
        dynamicStore
            .addTotalNetWeight(weight);
        break;
      case ENERGY:
        dynamicStore
            .addTotalEnergyWeight(weight);
        break;
      case TRON_POWER:
        dynamicStore
            .addTotalTronPowerWeight(weight);
        break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L123-191)
```java
      AccountCapsule accountCapsule = accountStore.get(ownerAddress);
      if (accountCapsule == null) {
        throw new ContractValidateException(
            ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
      }

      long tronPower;
      DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
      if (dynamicStore.supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }

      sum = LongMath
          .checkedMultiply(sum, TRX_PRECISION); //trx -> drop. The vote count is based on TRX
      if (sum > tronPower) {
        throw new ContractValidateException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }

    return true;
  }

  private void countVoteAccount(VoteWitnessContract voteContract) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    VotesStore votesStore = chainBaseManager.getVotesStore();
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    byte[] ownerAddress = voteContract.getOwnerAddress().toByteArray();

    VotesCapsule votesCapsule;

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    if (!votesStore.has(ownerAddress)) {
      votesCapsule = new VotesCapsule(voteContract.getOwnerAddress(),
          accountCapsule.getVotesList());
    } else {
      votesCapsule = votesStore.get(ownerAddress);
    }

    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
  }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L103-131)
```java
    Map<ByteString, Long> countWitness = countVote(votesStore);
    if (!countWitness.isEmpty()) {
      List<ByteString> currentWits = consensusDelegate.getActiveWitnesses();

      List<ByteString> newWitnessAddressList = new ArrayList<>();
      consensusDelegate.getAllWitnesses()
          .forEach(witnessCapsule -> newWitnessAddressList.add(witnessCapsule.getAddress()));

      countWitness.forEach((address, voteCount) -> {
        byte[] witnessAddress = address.toByteArray();
        WitnessCapsule witnessCapsule = consensusDelegate.getWitness(witnessAddress);
        if (witnessCapsule == null) {
          logger.warn("Witness capsule is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        AccountCapsule account = consensusDelegate.getAccount(witnessAddress);
        if (account == null) {
          logger.warn("Witness account is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount);
        consensusDelegate.saveWitness(witnessCapsule);
        logger.info("address is {} , countVote is {}", witnessCapsule.createReadableString(),
            witnessCapsule.getVoteCount());
      });

      dposService.updateWitness(newWitnessAddressList);

      incentiveManager.reward(newWitnessAddressList);
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-195)
```java
  private Map<ByteString, Long> countVote(VotesStore votesStore) {
    final Map<ByteString, Long> countWitness = Maps.newHashMap();
    Iterator<Entry<byte[], VotesCapsule>> dbIterator = votesStore.iterator();
    long sizeCount = 0;
    while (dbIterator.hasNext()) {
      Entry<byte[], VotesCapsule> next = dbIterator.next();
      VotesCapsule votes = next.getValue();
      votes.getOldVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) - voteCount);
        } else {
          countWitness.put(voteAddress, -voteCount);
        }
      });
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) + voteCount);
        } else {
          countWitness.put(voteAddress, voteCount);
        }
      });
      sizeCount++;
      votesStore.delete(next.getKey());
    }
    logger.info("There is {} new votes in this epoch", sizeCount);
    return countWitness;
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
