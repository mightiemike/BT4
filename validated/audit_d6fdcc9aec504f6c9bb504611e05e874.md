### Title
Owner's delegated TRX is permanently locked when receiver's `AcquiredDelegatedFrozenBalance` counter is smaller than the delegated amount - (File: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java`)

### Summary
`UnfreezeBalanceActuator.validate()` unconditionally reverts (`ContractValidateException`) when the receiver's `AcquiredDelegatedFrozenBalanceForBandwidth/Energy` counter is lower than the amount recorded in the `DelegatedResourceCapsule`, and this desync is a state the protocol itself can create (via TVM contract `suicide`/account recreation). Once created, the owner can never successfully call `UnfreezeBalanceContract` to reclaim the frozen TRX, exactly mirroring the reported bug class: a post-accrual state change on the "recipient" side (owner→address(0) for the NFT; receiver account deleted/recreated with a smaller counter for TRON) causes the withdrawal path to always revert, trapping the funds/resources permanently.

### Finding Description
When a user delegates frozen balance for bandwidth/energy to a `receiverAddress`, TRON tracks how much has been delegated in `DelegatedResourceCapsule` and mirrors it on the receiver account as `AcquiredDelegatedFrozenBalanceForBandwidth`/`Energy`. To reclaim the delegation (`UnfreezeBalanceContract` with `receiverAddress` set), `validate()` performs: [1](#0-0) 

If `dynamicStore.getAllowTvmConstantinople() == 0`, the legacy branch unconditionally throws when `receiverCapsule.getAcquiredDelegatedFrozenBalanceForXXX() < delegatedResourceCapsule.getFrozenBalanceForXXX()` — there is no fallback. Even in the newer branch, the exception is still thrown whenever `AllowTvmSolidity059 != 1` and the same inequality holds.

This desynchronized state is reachable in production: a `receiverAddress` that is (or later becomes) a TVM contract can self-destruct via `Program.suicide`, which deletes the account and does not preserve `AcquiredDelegatedFrozenBalance*` counters: [2](#0-1) 

If the receiver address is later recreated as a normal account (e.g. via a plain `TransferContract`, which auto-creates missing accounts with all fields zeroed), its `AcquiredDelegatedFrozenBalanceForEnergy`/`Bandwidth` starts at 0 (or some value smaller than the still-existing delegated amount), while the owner's `DelegatedResourceCapsule` entry still reflects the original delegated amount. The framework's own regression test demonstrates this exact scenario and its resulting permanent-revert behavior when the enabling proposal flags are off: [3](#0-2) 

The `AllowTvmSolidity059` proposal (a committee-controlled chain parameter) is the only mechanism that adds a clawback-style fallback (`receiverCapsule.setAcquiredDelegatedFrozenBalanceForXXX(0)` and proceeding anyway) in `execute()`: [4](#0-3) 

Without that proposal enabled (or on chains/points in time before it is activated), the owner's frozen TRX is permanently stuck: the delegated resource record can never be cleared and the corresponding frozen TRX balance can never be returned to the owner's spendable balance, exactly as in the reported NFT case where rewards became permanently irretrievable once the recipient state changed to an unreachable one.

### Impact Explanation
Impact is asset lock, not theft: once triggered, the affected owner's frozen TRX (potentially any amount delegated) becomes permanently unretrievable through the normal `UnfreezeBalanceContract` path, unless/until the committee activates the `AllowTvmSolidity059` proposal for that specific desync condition. This is an accounting/availability defect reachable from ordinary broadcast transactions (delegate → receiver self-destructs/recreates → owner attempts unfreeze) without any privileged actor.

### Likelihood Explanation
Requires: (1) the resource delegation feature enabled (`supportDR`), (2) a receiver address that is/becomes a contract capable of `suicide`, and (3) the chain not having activated the `AllowTvmSolidity059`/`AllowTvmConstantinople` fallback logic for this case (or accounts recreated with `AcquiredDelegatedFrozenBalance` smaller than the still-recorded delegated amount, which the "TVM contract suicide, re-create will produce this situation" comments in the newer `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` code confirm the tron team is aware of as a real, recurring situation). This is a narrow but concretely reachable path — not purely a design choice like the GR/guard-representative reward-withdrawal block, since it stems from an unhandled state mismatch rather than an intentional restriction.

### Recommendation
Apply the same clawback approach already used in the newer code paths (`UnDelegateResourceActuator`/`UnDelegateResourceProcessor`) universally in `UnfreezeBalanceActuator.validate()`/`execute()`: instead of reverting when `AcquiredDelegatedFrozenBalanceForXXX < delegatedResourceCapsule.getFrozenBalanceForXXX()`, always clamp the receiver's counter to 0 and let the owner reclaim the frozen balance, regardless of the `AllowTvmConstantinople`/`AllowTvmSolidity059` flag values, so the mitigation is not gated behind a committee proposal that may never be enabled on a given network.

### Proof of Concept
The existing regression test in the repository reproduces the exact locking condition (see `testUnfreezeDelegatedBalanceForCpuWithRecreatedReceiver`): delegate energy from `OWNER_ADDRESS` to `RECEIVER_ADDRESS`; delete and recreate `RECEIVER_ADDRESS` with a smaller `AcquiredDelegatedFrozenBalanceForEnergy` (10 vs. delegated `1000000000`); with `AllowShieldedTransaction`/`AllowTvmSolidity059` disabled, `actuator.validate()` throws `"AcquiredDelegatedFrozenBalanceForEnergy[10] < delegatedEnergy[1000000000]"` and `execute()` never completes, permanently blocking the owner from reclaiming the frozen TRX until the proposal flags are flipped on-chain. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L113-146)
```java
      AccountCapsule receiverCapsule = accountStore.get(receiverAddress);

      if (dynamicStore.getAllowTvmConstantinople() == 0 ||
          (receiverCapsule != null && receiverCapsule.getType() != AccountType.Contract)) {
        switch (unfreezeBalanceContract.getResource()) {
          case BANDWIDTH:
            long oldNetWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() / 
                    TRX_PRECISION;
            if (dynamicStore.getAllowTvmSolidity059() == 1
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < unfreezeBalance) {
              oldNetWeight = unfreezeBalance / TRX_PRECISION;
              receiverCapsule.setAcquiredDelegatedFrozenBalanceForBandwidth(0);
            } else {
              receiverCapsule.addAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance);
            }
            long newNetWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() / 
                    TRX_PRECISION;
            decrease = newNetWeight - oldNetWeight;
            break;
          case ENERGY:
            long oldEnergyWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() / 
                    TRX_PRECISION;
            if (dynamicStore.getAllowTvmSolidity059() == 1
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() < unfreezeBalance) {
              oldEnergyWeight = unfreezeBalance / TRX_PRECISION;
              receiverCapsule.setAcquiredDelegatedFrozenBalanceForEnergy(0);
            } else {
              receiverCapsule.addAcquiredDelegatedFrozenBalanceForEnergy(-unfreezeBalance);
            }
            long newEnergyWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() / 
                    TRX_PRECISION;
            decrease = newEnergyWeight - oldEnergyWeight;
            break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L373-394)
```java
          if (dynamicStore.getAllowTvmConstantinople() == 0) {
            if (receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < delegatedResourceCapsule.getFrozenBalanceForBandwidth()) {
              throw new ContractValidateException(
                  "AcquiredDelegatedFrozenBalanceForBandwidth[" + receiverCapsule
                      .getAcquiredDelegatedFrozenBalanceForBandwidth() + "] < delegatedBandwidth["
                      + delegatedResourceCapsule.getFrozenBalanceForBandwidth()
                      + "]");
            }
          } else {
            if (dynamicStore.getAllowTvmSolidity059() != 1
                && receiverCapsule != null
                && receiverCapsule.getType() != AccountType.Contract
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < delegatedResourceCapsule.getFrozenBalanceForBandwidth()) {
              throw new ContractValidateException(
                  "AcquiredDelegatedFrozenBalanceForBandwidth[" + receiverCapsule
                      .getAcquiredDelegatedFrozenBalanceForBandwidth() + "] < delegatedBandwidth["
                      + delegatedResourceCapsule.getFrozenBalanceForBandwidth()
                      + "]");
            }
          }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L505-516)
```java
    if (VMConfig.allowTvmFreezeV2()) {
      byte[] Inheritor =
          FastByteComparisons.isEqual(owner, obtainer)
              ? getContractState().getBlackHoleAddress()
              : obtainer;
      long expireUnfrozenBalance = transferFrozenV2BalanceToInheritor(owner, Inheritor, getContractState());
      if (expireUnfrozenBalance > 0 && internalTx != null) {
        internalTx.setValue(internalTx.getValue() + expireUnfrozenBalance);
      }
    }
    getResult().addDeleteAccount(this.getContractAddress());
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/UnfreezeBalanceActuatorTest.java (L747-822)
```java
  @Test
  public void testUnfreezeDelegatedBalanceForCpuWithRecreatedReceiver() {
    dbManager.getDynamicPropertiesStore().saveAllowDelegateResource(1);
    dbManager.getDynamicPropertiesStore().saveAllowTvmConstantinople(1);

    long now = System.currentTimeMillis();
    dbManager.getDynamicPropertiesStore().saveLatestBlockHeaderTimestamp(now);

    AccountCapsule owner = dbManager.getAccountStore()
        .get(ByteArray.fromHexString(OWNER_ADDRESS));
    owner.addDelegatedFrozenBalanceForEnergy(frozenBalance);
    Assert.assertEquals(frozenBalance, owner.getTronPower());

    AccountCapsule receiver = dbManager.getAccountStore()
        .get(ByteArray.fromHexString(RECEIVER_ADDRESS));
    receiver.addAcquiredDelegatedFrozenBalanceForEnergy(frozenBalance);
    Assert.assertEquals(0L, receiver.getTronPower());

    dbManager.getAccountStore().put(owner.createDbKey(), owner);

    DelegatedResourceCapsule delegatedResourceCapsule = new DelegatedResourceCapsule(
        owner.getAddress(),
        receiver.getAddress()
    );
    delegatedResourceCapsule.setFrozenBalanceForEnergy(
        frozenBalance,
        now - 100L);
    dbManager.getDelegatedResourceStore().put(DelegatedResourceCapsule
        .createDbKey(ByteArray.fromHexString(OWNER_ADDRESS),
            ByteArray.fromHexString(RECEIVER_ADDRESS)), delegatedResourceCapsule);

    UnfreezeBalanceActuator actuator = new UnfreezeBalanceActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getDelegatedContractForCpu(OWNER_ADDRESS, RECEIVER_ADDRESS));
    TransactionResultCapsule ret = new TransactionResultCapsule();

    dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(0);
    dbManager.getDynamicPropertiesStore().saveAllowTvmSolidity059(0);
    dbManager.getAccountStore().delete(receiver.createDbKey());
    receiver = new AccountCapsule(receiver.getAddress(), ByteString.EMPTY, AccountType.Normal);
    receiver.setAcquiredDelegatedFrozenBalanceForEnergy(10L);
    dbManager.getAccountStore().put(receiver.createDbKey(), receiver);
    receiver = dbManager.getAccountStore().get(receiver.createDbKey());
    Assert.assertEquals(10, receiver.getAcquiredDelegatedFrozenBalanceForEnergy());

    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.fail();
    } catch (ContractValidateException e) {
      Assert.assertEquals(
          "AcquiredDelegatedFrozenBalanceForEnergy[10] < delegatedEnergy[1000000000]",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.fail();
    }

    dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);
    dbManager.getDynamicPropertiesStore().saveAllowTvmSolidity059(1);

    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.assertEquals(code.SUCESS, ret.getInstance().getRet());
      AccountCapsule ownerResult =
          dbManager.getAccountStore().get(ByteArray.fromHexString(OWNER_ADDRESS));

      Assert.assertEquals(initBalance + frozenBalance, ownerResult.getBalance());
      Assert.assertEquals(0L, ownerResult.getTronPower());
      Assert.assertEquals(0L, ownerResult.getDelegatedFrozenBalanceForEnergy());
      receiver = dbManager.getAccountStore().get(receiver.createDbKey());
      Assert.assertEquals(0, receiver.getAcquiredDelegatedFrozenBalanceForEnergy());
    } catch (ContractValidateException | ContractExeException e) {
      Assert.fail();
    }
  }
```
