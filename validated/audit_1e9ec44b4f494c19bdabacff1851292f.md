## Finding: Missing minimum-amount enforcement in `UnfreezeBalanceV2Actuator` allows spam of trivial unfreeze operations

### Title
Missing minimum unfreeze amount validation enables spam of dust-value `UnfreezeBalanceV2Contract` transactions - (File: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java`)

### Summary
The reported LBTC bug class is: a state-changing withdrawal-style operation lacks a minimum-amount threshold, so it can be invoked repeatedly with dust-sized amounts, flooding downstream processing and burning resources for negligible value transferred. In java-tron, `UnfreezeBalanceV2Actuator` (and its TVM-precompile counterpart `UnfreezeBalanceV2Processor`) exhibits the same class of defect: the sibling operation `FreezeBalanceV2Actuator` enforces a minimum of `TRX_PRECISION` (1 TRX) on the frozen amount, but `UnfreezeBalanceV2Actuator.checkUnfreezeBalance` only requires `unfreezeBalance > 0`, with no lower bound.

### Finding Description
`FreezeBalanceV2Actuator.validate()` enforces: [1](#0-0) 

By contrast, `UnfreezeBalanceV2Actuator.checkUnfreezeBalance()` only checks that the amount is positive and does not exceed the frozen balance — there is no equivalent `TRX_PRECISION` floor: [2](#0-1) 

The same missing-minimum pattern exists in the native/TVM contract processor used by smart contracts invoking unfreeze via precompiles: [3](#0-2) 

Each call to `execute()` performs non-trivial work regardless of the amount unfrozen: it invokes `mortgageService.withdrawReward(ownerAddress)` (processing outstanding vote rewards), appends an entry to the account's `UnfrozenV2` list, recomputes total resource weight, and — when the account holds votes — iterates and rewrites the entire vote list in `updateVote()`: [4](#0-3) [5](#0-4) 

Because a user only needs a single 1 TRX `FreezeBalanceV2` operation to obtain a frozen balance, they can subsequently submit up to `UNFREEZE_MAX_TIMES` (32) `UnfreezeBalanceV2Contract` transactions per account, each unfreezing an amount as small as 1 sun, each one a fully-priced, fee-earning broadcast transaction that performs reward withdrawal, vote-list rewrites, and resource-weight bookkeeping: [6](#0-5) 

`calcFee()` for this actuator returns `0`, meaning these dust-value operations consume no economic disincentive beyond ordinary bandwidth/energy costs: [7](#0-6) 

### Impact Explanation
Unlike the freeze side, which enforces a 1 TRX floor to prevent creation of economically meaningless resource-delegation state, the unfreeze side has no floor. Combined with the unbounded reuse of many accounts (an attacker is not limited to one account), this allows generation of large numbers of near-zero-value `UnfreezeBalanceV2Contract` transactions that each still trigger full reward-withdrawal and vote-redistribution logic, wasting node processing/storage and inflating chain state (`UnfrozenV2` entries, delegated resource indexes) for no meaningful economic activity — the same "denial of service via unenforced minimum amount" bug class described in the report.

### Likelihood Explanation
Any account holder can freeze the minimum 1 TRX once and then repeatedly submit sub-TRX unfreeze transactions (bounded by `UNFREEZE_MAX_TIMES` per account, but unbounded across the set of accounts an attacker controls), making this reachable from an ordinary broadcast transaction with no special privilege.

### Recommendation
Add a minimum-amount check to `UnfreezeBalanceV2Actuator.checkUnfreezeBalance()` (and to `UnfreezeBalanceV2Processor.checkUnfreezeBalance()`) mirroring the `TRX_PRECISION` floor already enforced in `FreezeBalanceV2Actuator.validate()`, so dust-value unfreeze requests are rejected.

### Proof of Concept
1. Freeze exactly `TRX_PRECISION` (1 TRX) for `BANDWIDTH` via `FreezeBalanceV2Contract`.
2. Submit up to 32 `UnfreezeBalanceV2Contract` transactions each unfreezing `1` sun (passes `checkUnfreezeBalance` since `1 > 0` and `1 <= frozenAmount`).
3. Observe each transaction succeeds, appends an `UnFreezeV2` entry, and triggers `mortgageService.withdrawReward` and `updateVote` processing — repeatable across many attacker-controlled accounts to amplify load, with no minimum-amount rejection anywhere in the path. [8](#0-7)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L131-137)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L43-48)
```java
  @Getter
  private static final int UNFREEZE_MAX_TIMES = 32;

  public UnfreezeBalanceV2Actuator() {
    super(ContractType.UnfreezeBalanceV2Contract, UnfreezeBalanceV2Contract.class);
  }
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L192-195)
```java
  @Override
  public long calcFee() {
    return 0;
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L303-388)
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

    VotesCapsule votesCapsule;
    if (!votesStore.has(ownerAddress)) {
      votesCapsule = new VotesCapsule(
          unfreezeBalanceV2Contract.getOwnerAddress(),
          accountCapsule.getVotesList()
      );
    } else {
      votesCapsule = votesStore.get(ownerAddress);
    }

    // Update Owner Voting
    List<Vote> addVotes = new ArrayList<>();
    for (Vote vote : accountCapsule.getVotesList()) {
      long newVoteCount = (long)
          ((double) vote.getVoteCount() / totalVote * ownedTronPower / TRX_PRECISION);
      if (newVoteCount > 0) {
        Vote newVote = Vote.newBuilder()
            .setVoteAddress(vote.getVoteAddress())
            .setVoteCount(newVoteCount)
            .build();
        addVotes.add(newVote);
      }
    }
    votesCapsule.clearNewVotes();
    votesCapsule.addAllNewVotes(addVotes);
    votesStore.put(ownerAddress, votesCapsule);

    accountCapsule.clearVotes();
    accountCapsule.addAllVotes(addVotes);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L91-106)
```java
  private boolean checkUnfreezeBalance(
      AccountCapsule accountCapsule, long unfreezeBalance, Common.ResourceCode freezeType)  {
    if (unfreezeBalance <= 0) {
      return false;
    }
    long frozenBalance = 0L;
    List<Protocol.Account.FreezeV2> freezeV2List = accountCapsule.getFrozenV2List();
    for (Protocol.Account.FreezeV2 freezeV2 : freezeV2List) {
      if (freezeV2.getType().equals(freezeType)) {
        frozenBalance = freezeV2.getAmount();
        break;
      }
    }

    return unfreezeBalance <= frozenBalance;
  }
```
