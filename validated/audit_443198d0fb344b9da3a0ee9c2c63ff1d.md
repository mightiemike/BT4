### Title
Flash-loan-style Super Representative election manipulation via instant freeze→vote→unfreeze with `FreezeBalanceV2Contract` - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java])

### Summary
`FreezeBalanceV2Actuator` (the "Stake 2.0" freeze path) removed the mandatory minimum freeze-duration check that exists in the legacy `FreezeBalanceActuator`. Combined with the fact that `MaintenanceManager.doMaintenance()` applies vote deltas to a witness's vote count **cumulatively and permanently**, while any subsequent correction (from unfreezing) is only reflected at the **next** maintenance cycle, an attacker can temporarily inflate TRON Power, vote a Super Representative candidate into the active witness set, and withdraw the stake before the correction is applied — mirroring the flash-loan governance-manipulation pattern described in the report.

### Finding Description
Legacy freeze (`FreezeBalanceActuator.validate()`) enforces a minimum lock-up: [1](#0-0) 

The V2 freeze path (`FreezeBalanceV2Actuator.validate()`) has no such minimum duration check — the frozen balance and TRON Power are effective immediately and there is no lock period at all: [2](#0-1) 

`VoteWitnessActuator`/`VoteWitnessProcessor` allow voting up to the account's current `tronPower`/`getAllTronPower()`, computed instantaneously from currently frozen balance: [3](#0-2) 

Vote tallying happens only at maintenance boundaries via `MaintenanceManager.doMaintenance()` / `countVote()`, which reads the `VotesStore` (old vs new votes), **adds the delta cumulatively and permanently** to `witnessCapsule.getVoteCount()`, and then deletes the `VotesCapsule` entry: [4](#0-3) [5](#0-4) 

The new active witness set is computed immediately from this post-tally state: [6](#0-5) 

Crucially, when a voter later calls `UnfreezeBalanceV2Contract`, `updateVote()` only recomputes/reduces the account's vote allocation *at the time of unfreezing* and writes a new `VotesCapsule` (since the previous one was deleted at maintenance). That correction is only picked up at the **next** maintenance cycle's `countVote()` pass: [7](#0-6) 

Because `FreezeBalanceV2Contract` has no minimum lock-up, an unprivileged account can, within a single maintenance epoch:
1. Broadcast `FreezeBalanceV2Contract` (TRON_POWER) to instantly obtain large voting weight (no wait period, unlike legacy freeze).
2. Broadcast `VoteWitnessContract` for a target candidate.
3. Let the pending maintenance boundary tally the vote — this **permanently** (until the next epoch) bumps the witness's cumulative vote count and can flip the active SR set (`dposService.updateWitness`).
4. Immediately broadcast `UnfreezeBalanceV2Contract` to reclaim the capital; the correction to the witness's vote count is deferred to the following maintenance cycle, so the manipulated SR set remains active for a full epoch even though the backing stake has already been withdrawn.

This is functionally analogous to the flash-loan governance attack in the referenced report: transient capital is used to deterministically swing a governance/consensus decision (SR election) whose effect outlives the capital that produced it, and the correction mechanism lags by design.

### Impact Explanation
Manipulating the active Super Representative set is a consensus-level concern: SRs produce blocks, participate in PBFT finality (`pbftManager`), and receive block rewards. An attacker able to briefly and cheaply install a colluding/malicious witness into the active set for a full maintenance epoch (typically hours) gains block-production influence, potential censorship or reordering capability during that epoch, and reward extraction — all without permanently locking the capital used to win the seat, undermining the intended economic cost of influencing consensus.

### Likelihood Explanation
Reachable purely via three ordinary, unprivileged broadcast transactions (`FreezeBalanceV2Contract`, `VoteWitnessContract`, `UnfreezeBalanceV2Contract`) that any account can submit; no special permissions, keys, or node compromise required. The only requirement is having (or pooling, as the original report's judge noted) sufficient TRX for one maintenance window, and timing the freeze/vote shortly before a maintenance boundary — both of which are observable on-chain (`getNextMaintenanceTime`).

### Recommendation
- Reintroduce a minimum lock-up period for `FreezeBalanceV2Contract`/TRON_POWER freezes (mirroring the legacy `minFrozenTime` check), or require that TRON Power used for voting be based on a balance frozen for at least one full maintenance cycle before it counts toward `tronPower` in `VoteWitnessActuator`/`VoteWitnessProcessor`.
- Alternatively, snapshot voting weight at the start of a maintenance epoch (rather than allowing votes based on the instantaneous frozen balance) so that freeze→vote→unfreeze within the same epoch cannot influence that epoch's tally.
- Ensure vote corrections from unfreezing are applied atomically/synchronously rather than deferred to the next `doMaintenance()` cycle, or block unfreezing of TRON_POWER-designated balance that has outstanding votes until the following maintenance has processed the reduction.

### Proof of Concept
1. Account A has 0 initial power, obtains capital `N` TRX (own or pooled per the referenced report's threat model).
2. A broadcasts `FreezeBalanceV2Contract{resource=TRON_POWER, frozenBalance=N}` — TRON Power becomes available immediately (`FreezeBalanceV2Actuator`, no `minFrozenTime` check, unlike `FreezeBalanceActuator`).
3. A broadcasts `VoteWitnessContract` voting `N/TRX_PRECISION` for target witness W, shortly before `nextMaintenanceTime`.
4. `MaintenanceManager.doMaintenance()` fires: `countVote()` adds A's vote to `witnessCapsule.getVoteCount()` for W permanently and clears `VotesStore`; `dposService.updateWitness()` recomputes the active SR set, potentially adding W.
5. A immediately broadcasts `UnfreezeBalanceV2Contract` for the full `N`. `updateVote()` reduces A's vote allocation, but this only writes a fresh `VotesCapsule`; W's already-tallied vote count and active-SR status are unaffected until the *next* maintenance cycle.
6. W remains in the active witness set (producing blocks / earning rewards) for the remainder of the epoch despite A having withdrawn all backing capital.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L203-214)
```java
    long frozenDuration = freezeBalanceContract.getFrozenDuration();
    long minFrozenTime = dynamicStore.getMinFrozenTime();
    long maxFrozenTime = dynamicStore.getMaxFrozenTime();

    boolean needCheckFrozeTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1;//for test
    if (needCheckFrozeTime && !(frozenDuration >= minFrozenTime
        && frozenDuration <= maxFrozenTime)) {
      throw new ContractValidateException(
          "frozenDuration must be less than " + maxFrozenTime + " days "
              + "and more than " + minFrozenTime + " days");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L92-164)
```java
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (!any.is(FreezeBalanceV2Contract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [FreezeBalanceV2Contract],real type[" + any
              .getClass() + "]");
    }

    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support FreezeV2 transaction,"
          + " need to be opened by the committee");
    }

    final FreezeBalanceV2Contract freezeBalanceV2Contract;
    try {
      freezeBalanceV2Contract = this.any.unpack(FreezeBalanceV2Contract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
    byte[] ownerAddress = freezeBalanceV2Contract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }

    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }

    if (frozenBalance > accountCapsule.getBalance()) {
      throw new ContractValidateException("frozenBalance must be less than or equal to accountBalance");
    }

    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
      case ENERGY:
        break;
      case TRON_POWER:
        if (!dynamicStore.supportAllowNewResourceModel()) {
          throw new ContractValidateException(
              "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
        break;
      default:
        if (dynamicStore.supportAllowNewResourceModel()) {
          throw new ContractValidateException(
              "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY、TRON_POWER]");
        } else {
          throw new ContractValidateException(
              "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L129-146)
```java
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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L103-127)
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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-192)
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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/DposService.java (L178-186)
```java
  public void updateWitness(List<ByteString> list) {
    consensusDelegate.sortWitness(list);
    if (list.size() > MAX_ACTIVE_WITNESS_NUM) {
      consensusDelegate
          .saveActiveWitnesses(list.subList(0, MAX_ACTIVE_WITNESS_NUM));
    } else {
      consensusDelegate.saveActiveWitnesses(list);
    }
  }
```

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
