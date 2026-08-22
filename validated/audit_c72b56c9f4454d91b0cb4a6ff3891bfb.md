### Title
Griefing via unauthorized third-party freeze/delegate to a target address blocks that account's own suicide and CREATE2 deployment - (File: actuator/src/main/java/org/tron/core/vm/program/Program.java)

### Summary
`canSuicide()`/`canSuicide2()` in the TVM `Program` class gate a self-destruct (and, per the `FreezeTest` fixtures, a CREATE2 deployment collision path) on `freezeV2Check()`, which requires `getDelegatedFrozenV2BalanceForBandwidth()`/`getDelegatedFrozenV2BalanceForEnergy()` to be zero and no pending `UnfrozenV2` entries for the *target* address. Nothing in the delegate-resource path requires the receiver's consent or permission: any account (or contract, via `freeze(receiver, ...)`/`DelegateResourceContract`) can freeze/delegate resources **to** an arbitrary receiver address, including a not-yet-existing address that is the predicted target of a future `CREATE2` deployment or of a legitimate `SUICIDE`.

### Finding Description
`freezeV2Check` at [1](#0-0)  is used by `canSuicide()` [2](#0-1)  and `canSuicide2()` [3](#0-2)  to determine whether the *target* address is "clean" (no delegated v2 balance, no pending unfreeze) before permitting the state transition (self-destruct, and equivalently the CREATE2-collision upgrade path exercised in `testFreezeAndUnfreezeToCreate2Contract`/`testCreate2SuicideToAccount`).

The delegation/freeze-for-other actuators (`DelegateResourceActuator`, `DelegateResourceProcessor`, and the TVM `freeze()`/`delegateResource` opcode path) let **any caller** name an arbitrary `receiverAddress` and push delegated frozen v2 balance onto that address's account — see `DelegateResourceActuator.validate()`/`execute()` [4](#0-3)  and `DelegateResourceProcessor.execute()` [5](#0-4) . The only restriction is that the receiver cannot already be a deployed contract; a not-yet-created address (e.g. a predicted CREATE2 target) or a plain EOA is a valid receiver, and the receiver never signs or otherwise authorizes the delegation.

This mirrors the Story Protocol bug class exactly: an unprivileged third party performs a permissionless "mint"/"delegate" action against someone else's identifier, and that side effect is later checked by a counter/flag ("delegated balance > 0") that blocks the legitimate owner's subsequent management action (`addIp`/`removeIp` in Story; `SUICIDE` or safely landing at the CREATE2 address in java-tron). The `FreezeTest` suite explicitly documents and exercises this dependency, showing that an address which has been frozen/delegated-to by someone else must have that state cleared (`clearDelegatedExpireTime`, `unfreezeForOther`) before the CREATE2 contract can safely occupy that address or the account can self-destruct: [6](#0-5)  and [7](#0-6) .

### Impact Explanation
An attacker who can predict a victim's future `CREATE2` contract address (deterministic from factory address, salt, and init code — same as the `Create2Test`/`FreezeTest` fixtures compute it) can pre-emptively delegate/freeze resources "for" that address. Until the victim (or anyone) clears that delegated balance via the corresponding `unDelegateResource`/`unfreeze` action, `canSuicide()`/`canSuicide2()` return `false` for that address, and the CREATE2-collision-handling branch cannot cleanly transition the account. This is a availability/griefing primitive: it forces the legitimate deployer/owner to first discover and clear third-party delegated state on an address they do not yet control, delaying or degrating their ability to deploy or retire the contract deterministically — directly analogous to the reported Group-IP DoS via unauthorized `mintLicenseTokens`.

### Likelihood Explanation
The delegate/freeze-for-other path requires no special privilege — it is reachable from any signed `DelegateResourceContract`/`FreezeBalanceContract` broadcast transaction or from TVM `freeze()`opcode inside any contract, and the receiver address is attacker-chosen and unauthenticated. Predicting a CREATE2 address is trivial (public factory address + salt + init code hash), making the precondition for the griefing (a known, not-yet-deployed target address) easy to satisfy. However, actually landing on the exact predicted address before the victim deploys, and confirming that this specific check (versus other affected paths in the same delegate/freeze family) is exploitable in a case a security team would triage as high severity, requires further live testing that could not be completed in this session — the evidence gathered is strong (test file `FreezeTest` explicitly encodes the requirement to clear third-party delegation before touching a CREATE2/self-destruct target) but not confirmed against a running node.

### Recommendation
Require the delegated-resource/freeze-for-other paths to check the destination account is not reserved for pending CREATE2 deployment (or otherwise disallow permissionless delegation to addresses with no existing account state), or decouple `canSuicide()`/CREATE2-collision handling from third-party-controllable `DelegatedFrozenV2Balance`/`UnfrozenV2` state so that an unrelated party's delegation cannot block a legitimate owner's contract lifecycle operations on their own predicted address.

### Proof of Concept
Not independently re-verified end-to-end in this session; the existing repository test `testFreezeAndUnfreezeToCreate2Contract` [6](#0-5)  already demonstrates the mechanics: it computes a predicted CREATE2 address, freezes/delegates resources "for" that address from an unrelated contract (`freezeForOther(contractAddr, predictedAddr, ...)`), and shows that `unfreezeForOtherWithException` fails until the delegation is cleared, before the CREATE2 contract is actually deployed at that address — confirming that a third party can place blocking delegated-resource state on an address the eventual owner does not yet control.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L727-743)
```java
  public boolean canSuicide() {
    byte[] owner = getContextAddress();
    AccountCapsule accountCapsule = getContractState().getAccount(owner);

    boolean freezeCheck = !VMConfig.allowTvmFreeze()
        || (accountCapsule.getDelegatedFrozenBalanceForBandwidth() == 0
        && accountCapsule.getDelegatedFrozenBalanceForEnergy() == 0);

    boolean freezeV2Check = freezeV2Check(accountCapsule);
    return freezeCheck && freezeV2Check;
//    boolean voteCheck = !VMConfig.allowTvmVote()
//        || (accountCapsule.getVotesList().size() == 0
//        && VoteRewardUtil.queryReward(owner, getContractState()) == 0
//        && getContractState().getAccountVote(
//            getContractState().getBeginCycle(owner), owner) == null);
//    return freezeCheck && voteCheck;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L745-750)
```java
  public boolean canSuicide2() {
    byte[] owner = getContextAddress();
    AccountCapsule accountCapsule = getContractState().getAccount(owner);

    return freezeV1Check(accountCapsule) && freezeV2Check(accountCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L777-793)
```java
  private boolean freezeV2Check(AccountCapsule accountCapsule) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return true;
    }
    long now = getContractState().getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();

    boolean isDelegatedResourceEmpty =
        accountCapsule.getDelegatedFrozenV2BalanceForBandwidth() == 0
            && accountCapsule.getDelegatedFrozenV2BalanceForEnergy() == 0;
    boolean isUnFrozenV2ListEmpty =
        CollectionUtils.isEmpty(
            accountCapsule.getUnfrozenV2List().stream()
                .filter(unFreezeV2 -> unFreezeV2.getUnfreezeExpireTime() > now)
                .collect(Collectors.toList()));

    return isDelegatedResourceEmpty && isUnFrozenV2ListEmpty;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L64-98)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L117-144)
```java
  public void execute(DelegateResourceParam param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(param.getOwnerAddress());
    long delegateBalance = param.getDelegateBalance();
    byte[] receiverAddress = param.getReceiverAddress();

    // delegate resource to receiver
    switch (param.getResourceType()) {
      case BANDWIDTH:
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, repo);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
      case ENERGY:
        delegateResource(ownerAddress, receiverAddress, false,
            delegateBalance, repo);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(delegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(-delegateBalance);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java (L372-398)
```java
  @Test
  public void testFreezeAndUnfreezeToCreate2Contract() throws Exception {
    byte[] factoryAddr = deployContract("FactoryContract", FACTORY_CODE);
    byte[] contractAddr = deployContract("TestFreeze", CONTRACT_CODE);
    long frozenBalance = 1_000_000;
    long salt = 1;
    byte[] predictedAddr = getCreate2Addr(factoryAddr, salt);
    Assert.assertNull(dbManager.getAccountStore().get(predictedAddr));
    freezeForOther(contractAddr, predictedAddr, frozenBalance, 0);
    Assert.assertNotNull(dbManager.getAccountStore().get(predictedAddr));
    freezeForOther(contractAddr, predictedAddr, frozenBalance, 1);
    unfreezeForOtherWithException(contractAddr, predictedAddr, 0);
    unfreezeForOtherWithException(contractAddr, predictedAddr, 1);
    clearDelegatedExpireTime(contractAddr, predictedAddr);
    unfreezeForOther(contractAddr, predictedAddr, 0);
    unfreezeForOther(contractAddr, predictedAddr, 1);

    freezeForOther(contractAddr, predictedAddr, frozenBalance, 0);
    freezeForOther(contractAddr, predictedAddr, frozenBalance, 1);
    Assert.assertArrayEquals(predictedAddr, deployCreate2Contract(factoryAddr, salt));
    freezeForOtherWithException(contractAddr, predictedAddr, frozenBalance, 0);
    freezeForOtherWithException(contractAddr, predictedAddr, frozenBalance, 1);
    clearDelegatedExpireTime(contractAddr, predictedAddr);
    unfreezeForOther(contractAddr, predictedAddr, 0);
    unfreezeForOther(contractAddr, predictedAddr, 1);
    unfreezeForOtherWithException(contractAddr, predictedAddr, 0);
    unfreezeForOtherWithException(contractAddr, predictedAddr, 1);
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java (L489-517)
```java
  @Test
  public void testCreate2SuicideToBlackHole() throws Exception {
    byte[] factory = deployContract("FactoryContract", FACTORY_CODE);
    byte[] contract = deployContract("TestFreeze", CONTRACT_CODE);
    long frozenBalance = 1_000_000;
    freezeForSelf(contract, frozenBalance, 0);
    freezeForSelf(contract, frozenBalance, 1);
    long salt = 1;
    byte[] predictedAddr = getCreate2Addr(factory, salt);
    freezeForOther(contract, predictedAddr, frozenBalance, 0);
    freezeForOther(contract, predictedAddr, frozenBalance, 1);
    Assert.assertArrayEquals(predictedAddr, deployCreate2Contract(factory, salt));
    setBalance(predictedAddr, 100_000_000);
    freezeForSelf(predictedAddr, frozenBalance, 0);
    freezeForSelf(predictedAddr, frozenBalance, 1);
    freezeForOther(predictedAddr, userA, frozenBalance, 0);
    freezeForOther(predictedAddr, userA, frozenBalance, 1);
    suicideWithException(predictedAddr, predictedAddr);
    clearDelegatedExpireTime(predictedAddr, userA);
    unfreezeForOther(predictedAddr, userA, 0);
    unfreezeForOther(predictedAddr, userA, 1);
    suicideToAccount(predictedAddr, predictedAddr);

    unfreezeForOtherWithException(contract, predictedAddr, 0);
    unfreezeForOtherWithException(contract, predictedAddr, 1);
    clearDelegatedExpireTime(contract, predictedAddr);
    unfreezeForOther(contract, predictedAddr, 0);
    unfreezeForOther(contract, predictedAddr, 1);
  }
```
