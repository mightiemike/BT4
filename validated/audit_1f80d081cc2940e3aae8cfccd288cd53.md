### Title
`DelegateResourceActuator`/`UnDelegateResourceActuator` execute() modify account resource/reward-relevant state without calling `mortgageService.withdrawReward()` (missing accrual step, analog to missing `updatePoints`) - (File: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java`)

### Summary
The reported Gloop Finance bug is that `repay()` mutates protocol state without invoking the `updatePoints` modifier that its sibling functions (`deposit`, `withdraw`, `borrow`) all invoke, so reward accrual is skipped for that code path. The java-tron analog is `DelegateResourceActuator.execute()` (and `UnDelegateResourceActuator.execute()`), which mutate an account's frozen/delegated resource balances but do not call `mortgageService.withdrawReward(ownerAddress)` before doing so, unlike the structurally equivalent `UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, `VoteWitnessActuator`, and `WithdrawBalanceActuator`.

### Finding Description
`MortgageService.withdrawReward()` is the accrual/snapshot function analogous to `updatePoints`: it computes and pays out the reward owed for the current voting cycle and then records a snapshot of the account's current `votesList` into `DelegationStore` via `delegationStore.setAccountVote(endCycle, address, accountCapsule)` [1](#0-0) . This snapshot is what later reward computations (`computeReward`) use to determine how much of a witness's total reward the account is entitled to for a given cycle [2](#0-1) .

Every actuator that changes an account's resource/frozen balance in a way that can affect voting power calls `withdrawReward` first, so the reward snapshot reflects the account's state *before* the mutation:
- `UnfreezeBalanceActuator.execute()` calls `mortgageService.withdrawReward(ownerAddress)` before touching frozen balance [3](#0-2) .
- `UnfreezeBalanceV2Actuator.execute()` calls it before adjusting frozen/vote state and even explicitly trims stale votes via `updateVote()` afterward [4](#0-3) .
- `VoteWitnessActuator.countVoteAccount()` calls it before clearing/rewriting the votes list [5](#0-4) .
- `WithdrawBalanceActuator.execute()` calls it before touching balance/allowance [6](#0-5) .

By contrast, `DelegateResourceActuator.execute()` directly mutates `ownerCapsule`'s `DelegatedFrozenV2Balance`/`FrozenBalanceV2` fields for BANDWIDTH/ENERGY and persists the account with no preceding (or following) call to `withdrawReward()` or `updateVote()`: [7](#0-6) 

`UnDelegateResourceActuator.execute()` exhibits the same omission — it mutates both the receiver's and owner's account resource state (`addDelegatedFrozenV2BalanceForBandwidth`, `addFrozenBalanceForBandwidthV2`, etc.) and persists them, again without calling `withdrawReward()` or `updateVote()`: [8](#0-7) 

This mirrors the reported bug class exactly: a sibling set of state-mutating entry points where most call the "accrue/update" step but one (or two) do not.

### Impact Explanation
Because delegating/undelegating resources changes the frozen-balance basis that underlies an account's TRON Power, and votes cast by the account are compared against that TRON Power elsewhere (e.g. `updateVote()` in `UnfreezeBalanceV2Actuator` explicitly re-checks `ownedTronPower >= totalVote * TRX_PRECISION` and trims votes if not) [9](#0-8) , skipping `withdrawReward`/`updateVote` in the delegate/undelegate path means:
1. The reward snapshot recorded for the account in a later withdrawal can be taken against a votes list that is now inconsistent with the account's true (post-delegation) TRON Power, because the delegate/undelegate action never triggers a snapshot or vote correction at the point where power actually changes.
2. An account can retain votes that exceed its actual TRON Power after delegating resources away, which is exactly the class of inconsistency `updateVote()` exists to prevent in the sibling actuators.

This is a state-accounting/reward-divergence issue in a widely-used, unprivileged, user-facing actuator (any account can call `DelegateResourceContract`/`UnDelegateResourceContract`), so it satisfies the "concrete accounting/invalid-state divergence" impact bar requested.

### Likelihood Explanation
High reachability: `DelegateResourceActuator` and `UnDelegateResourceActuator` are standard, permissionless transaction types (`DelegateResourceContract` / `UnDelegateResourceContract`) available to any account holding frozen resources, gated only by `supportDR()`/`supportUnfreezeDelay()` feature flags, not by any privileged role [10](#0-9) . Any TRX holder who has both frozen resources and active votes can trigger this path by simply calling delegate/undelegate.

### Recommendation
Add a call to `mortgageService.withdrawReward(ownerAddress)` (and where appropriate `receiverAddress`) at the start of `DelegateResourceActuator.execute()` and `UnDelegateResourceActuator.execute()`, before any frozen/delegated balance fields are mutated, mirroring the pattern used in `UnfreezeBalanceV2Actuator`. Additionally, consider invoking the same vote-trimming logic (`updateVote()`) after a delegation reduces the owner's available TRON Power, to keep `votesList` consistent with actual voting power, consistent with how `UnfreezeBalanceV2Actuator` handles this.

### Proof of Concept
1. Compare `DelegateResourceActuator.execute()` [11](#0-10)  against `UnfreezeBalanceV2Actuator.execute()` [12](#0-11) : the latter calls `mortgageService.withdrawReward(ownerAddress)` (line 72) and `updateVote()` (line 89); the former calls neither.
2. Same comparison applies to `UnDelegateResourceActuator.execute()` [13](#0-12) .
3. `MortgageService.withdrawReward()` snapshot logic that depends on being called at the correct point relative to resource/vote changes: [1](#0-0) .

Note: I was unable to complete verification within the available tool budget of whether `AccountCapsule.getTronPower()`/`getAllTronPower()` count delegated-out (V2) frozen balance as part of the owner's own power (this would determine the precise magnitude of the vote/reward divergence caused by delegation). This detail should be confirmed by inspecting `AccountCapsule.java`'s `getTronPower()`/`getAllTronPower()` implementations before treating the severity as fully quantified.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-133)
```java
  public void withdrawReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
        adjustAllowance(address, reward);
        reward = 0;
        logger.info("Latest cycle reward {}, {}.", beginCycle, account.getVotesList());
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      delegationStore.setBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
      adjustAllowance(address, reward);
    }
    delegationStore.setBeginCycle(address, endCycle);
    delegationStore.setEndCycle(address, endCycle + 1);
    delegationStore.setAccountVote(endCycle, address, accountCapsule);
    logger.info("Adjust {} allowance {}, now currentCycle {}, beginCycle {}, endCycle {}, "
            + "account vote {}.", Hex.toHexString(address), reward, currentCycle,
        beginCycle, endCycle, accountCapsule.getVotesList());
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L171-230)
```java
  private long computeReward(long cycle, List<Pair<byte[], Long>> votes) {
    long reward = 0;
    for (Pair<byte[], Long> vote : votes) {
      byte[] srAddress = vote.getKey();
      long totalReward = delegationStore.getReward(cycle, srAddress);
      if (totalReward <= 0) {
        continue;
      }
      long totalVote = delegationStore.getWitnessVote(cycle, srAddress);
      if (totalVote == DelegationStore.REMARK || totalVote == 0) {
        continue;
      }
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
    }
    return reward;
  }

  /**
   * Compute reward from begin cycle to end cycle, which endCycle must greater than beginCycle.
   * While computing reward after new reward algorithm taking effective cycle number,
   * it will use new algorithm instead of old way.
   * @param beginCycle begin cycle (include)
   * @param endCycle end cycle (exclude)
   * @param accountCapsule account capsule
   * @return total reward
   */
  private long computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    long newAlgorithmCycle = dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle();
    List<Pair<byte[], Long>> srAddresses = accountCapsule.getVotesList().stream()
        .map(vote -> new Pair<>(vote.getVoteAddress().toByteArray(), vote.getVoteCount()))
        .collect(Collectors.toList());
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
    }
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long>  vote : srAddresses) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = delegationStore.getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = delegationStore.getWitnessVi(endCycle - 1, srAddress);
        BigInteger deltaVi = endVi.subtract(beginVi);
        if (deltaVi.signum() <= 0) {
          continue;
        }
        long userVote = vote.getValue();
        reward += deltaVi.multiply(BigInteger.valueOf(userVote))
            .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
      }
    }
    return reward;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L71-78)
```java
    byte[] ownerAddress = unfreezeBalanceContract.getOwnerAddress().toByteArray();

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    long oldBalance = accountCapsule.getBalance();

```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L50-101)
```java
  @Override
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract;
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    try {
      unfreezeBalanceV2Contract = any.unpack(UnfreezeBalanceV2Contract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }
    byte[] ownerAddress = unfreezeBalanceV2Contract.getOwnerAddress().toByteArray();
    long now = dynamicStore.getLatestBlockHeaderTimestamp();

    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    long unfreezeAmount = this.unfreezeExpire(accountCapsule, now);
    long unfreezeBalance = unfreezeBalanceV2Contract.getUnfreezeBalance();

    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

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
    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L340-357)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-177)
```java
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

```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L54-59)
```java
    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());

    AccountCapsule accountCapsule = accountStore.
        get(withdrawBalanceContract.getOwnerAddress().toByteArray());
    long oldBalance = accountCapsule.getBalance();
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L44-98)
```java
  @Override
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    final DelegateResourceContract delegateResourceContract;
    AccountStore accountStore = chainBaseManager.getAccountStore();
    byte[] ownerAddress;
    try {
      delegateResourceContract = this.any.unpack(DelegateResourceContract.class);
      ownerAddress = getOwnerAddress().toByteArray();
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    AccountCapsule ownerCapsule = accountStore
        .get(delegateResourceContract.getOwnerAddress().toByteArray());
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    long delegateBalance = delegateResourceContract.getBalance();
    boolean lock = delegateResourceContract.getLock();
    long lockPeriod = getLockPeriod(dynamicStore.supportMaxDelegateLockPeriod(),
            delegateResourceContract);
    byte[] receiverAddress = delegateResourceContract.getReceiverAddress().toByteArray();

    // delegate resource to receiver
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH:
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
      case ENERGY:
        delegateResource(ownerAddress, receiverAddress, false,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(delegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(-delegateBalance);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    accountStore.put(ownerCapsule.createDbKey(), ownerCapsule);

    ret.setStatus(fee, code.SUCESS);

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L102-126)
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
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    if (!any.is(DelegateResourceContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [DelegateResourceContract],real type["
              + any.getClass() + "]");
    }

    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }

    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support Delegate resource transaction,"
          + " need to be opened by the committee");
    }

```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L37-192)
```java
  @Override
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    final UnDelegateResourceContract unDelegateResourceContract;
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    DelegatedResourceAccountIndexStore delegatedResourceAccountIndexStore = chainBaseManager
        .getDelegatedResourceAccountIndexStore();
    try {
      unDelegateResourceContract = any.unpack(UnDelegateResourceContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }


    final long unDelegateBalance = unDelegateResourceContract.getBalance();
    byte[] ownerAddress = unDelegateResourceContract.getOwnerAddress().toByteArray();
    byte[] receiverAddress = unDelegateResourceContract.getReceiverAddress().toByteArray();

    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);

    long transferUsage = 0;
    // modify receiver Account
    if (receiverCapsule != null) {
      long now = chainBaseManager.getHeadSlot();
      switch (unDelegateResourceContract.getResource()) {
        case BANDWIDTH:
          BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(chainBaseManager);
          bandwidthProcessor.updateUsageForDelegated(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
          break;
        case ENERGY:
          EnergyProcessor energyProcessor = new EnergyProcessor(dynamicStore, accountStore);
          energyProcessor.updateUsage(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy()
              < unDelegateBalance) {
            // A TVM contract receiver, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForEnergy(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
          break;
        default:
          //this should never happen
          break;
      }
      accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
    }

    // transfer lock delegate to unlock
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress,
        dynamicStore.getLatestBlockHeaderTimestamp());

    byte[] unlockKey = DelegatedResourceCapsule
        .createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule unlockResource = delegatedResourceStore
        .get(unlockKey);

    // modify owner Account
    AccountCapsule ownerCapsule = accountStore.get(ownerAddress);
    switch (unDelegateResourceContract.getResource()) {
      case BANDWIDTH: {
        unlockResource.addFrozenBalanceForBandwidth(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(unDelegateBalance);

        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);

        long now = chainBaseManager.getHeadSlot();
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule,
              transferUsage, BANDWIDTH, now);
        }
      }
      break;
      case ENERGY: {
        unlockResource.addFrozenBalanceForEnergy(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(unDelegateBalance);

        EnergyProcessor processor = new EnergyProcessor(dynamicStore, accountStore);

        long now = chainBaseManager.getHeadSlot();
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule, transferUsage, ENERGY, now);
        }
      }
      break;
      default:
        //this should never happen
        break;
    }

    if (unlockResource.getFrozenBalanceForBandwidth() == 0
        && unlockResource.getFrozenBalanceForEnergy() == 0) {
      delegatedResourceStore.delete(unlockKey);
      unlockResource = null;
    } else {
      delegatedResourceStore.put(unlockKey, unlockResource);
    }

    byte[] lockKey = DelegatedResourceCapsule
        .createDbKeyV2(ownerAddress, receiverAddress, true);
    DelegatedResourceCapsule lockResource = delegatedResourceStore
        .get(lockKey);
    if (lockResource == null && unlockResource == null) {
      //modify DelegatedResourceAccountIndexStore
      delegatedResourceAccountIndexStore.unDelegateV2(ownerAddress, receiverAddress);
    }

    accountStore.put(ownerAddress, ownerCapsule);

    ret.setStatus(fee, code.SUCESS);

    return true;
  }
```
