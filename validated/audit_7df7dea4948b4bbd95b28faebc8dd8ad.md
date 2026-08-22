### Title
Contract `origin_energy_limit` Can Be Changed Mid-Block, Causing Inconsistent Energy-Fee Accounting Between Callers of the Same Contract - (File: `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java`)

### Summary
`UpdateEnergyLimitContract` lets a smart-contract's deployer ("origin" account) change `origin_energy_limit` — the cap on energy that can be charged to the contract owner instead of the caller — at any time via an ordinary broadcast transaction, with no restriction to apply the change only between blocks/rounds. This mirrors the Revolver.sol bug class of mutating a fee/threshold parameter mid-round: transactions calling the same contract within the same block (or even the same transaction batch) can be charged inconsistently depending on when in the block the update lands, because the value is read fresh from `ContractCapsule`/cache on each execution with no versioning or effective-after mechanism.

### Finding Description
`UpdateEnergyLimitContractActuator.execute()` unconditionally overwrites the deployed contract's `origin_energy_limit` and immediately evicts the LRU cache entry so the new value takes effect on the very next transaction: [1](#0-0) 

Validation only checks that the caller is the contract's origin address and that the new limit is `> 0`; it does not check whether the current block/round has already started processing calls to that contract, nor does it defer the change to a future block: [2](#0-1) [3](#0-2) 

`origin_energy_limit` is stored on the `ContractCapsule` and read live by energy-metering code (`VMActuator`, `ReceiptCapsule.checkForEnergyLimit`, `TransactionTrace`) each time the contract is invoked — there is no per-block/per-cycle snapshot equivalent to the cycle-indexed brokerage mechanism used elsewhere in java-tron (e.g., `DelegationStore.setBrokerage(cycle, address, brokerage)` staged for the *next* cycle in `MaintenanceManager.doMaintenance`). That existing pattern shows the project already knows how to defer "round-sensitive" parameter changes, but it was not applied to `origin_energy_limit`.

The test suite explicitly demonstrates that two sequential updates to the same contract both take effect immediately with no delay: [4](#0-3) 

### Impact Explanation
Because the change is applied instantly and is not versioned per block, transactions in flight within the same block (or block being packed by a witness) that call the affected contract can be charged energy fees under two different `origin_energy_limit` policies, depending purely on ordering relative to the `UpdateEnergyLimitContract` transaction. This can:
- Cause some callers to be billed unexpectedly higher TRX-equivalent fees (if the origin's energy-sharing cap drops mid-block) because less of their energy cost is absorbed by the contract owner than they expected when submitting the transaction.
- Allow a contract owner to grief users mid-block by lowering the shared energy limit right after enticing calls, or to game rebates by raising/lowering the limit around specific transactions they control, all while other users' transactions execute under stale assumptions.
- Produce accounting inconsistencies between transactions that should logically belong to the same "round" (block) of interaction with the contract, directly matching the reported bug class of "critical resource-accounting variables changed mid-round."

### Likelihood Explanation
Any account that deployed a smart contract can send `UpdateEnergyLimitContract` at will and it is processed like a normal transaction — no committee approval or maintenance-cycle gating is required, only `ReceiptCapsule.checkForEnergyLimit` (a hard-fork/feature flag) and ordinary address ownership checks: [5](#0-4) 
This makes exploitation trivial for any contract owner and requires no elevated node-level privilege, only ordinary broadcast-transaction access, which is fully within an anonymous/unprivileged reach relative to other users of the contract.

### Recommendation
- Snapshot `origin_energy_limit` at the start of each block (or defer updates to the next block/cycle, mirroring the `DelegationStore` cycle-staged brokerage pattern) so all transactions within a block use a consistent value.
- Alternatively, record the effective-from block number alongside the new limit and have energy metering code use the limit that was active at the time the containing block began, rather than the live/cached value.

### Proof of Concept
1. Contract owner deploys contract `C` with `origin_energy_limit = L1`.
2. Owner submits transaction `T1 = UpdateEnergyLimitContract(C, L2)` where `L2 << L1`.
3. Two user transactions `Tx_a` (submitted before `T1` is broadcast) and `Tx_b` (submitted after) both call `C` and land in the same block, with `Tx_a` ordered before `T1` and `Tx_b` ordered after `T1`.
4. Because `execute()` in `UpdateEnergyLimitContractActuator` updates `ContractCapsule` and purges the cache immediately (`RepositoryImpl.removeLruCache`), `Tx_a` is metered under `L1` while `Tx_b` is metered under `L2`, in the same block — demonstrating the mid-round inconsistency, confirmed by the actuator's immediate-effect behavior shown in `twiceUpdateEnergyLimitContract` test: [4](#0-3)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L40-48)
```java
      UpdateEnergyLimitContract usContract = any.unpack(UpdateEnergyLimitContract.class);
      long newOriginEnergyLimit = usContract.getOriginEnergyLimit();
      byte[] contractAddress = usContract.getContractAddress().toByteArray();
      ContractCapsule deployedContract = contractStore.get(contractAddress);

      contractStore.put(contractAddress, new ContractCapsule(
          deployedContract.getInstance().toBuilder().setOriginEnergyLimit(newOriginEnergyLimit)
              .build()));
      RepositoryImpl.removeLruCache(contractAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L59-70)
```java
  @Override
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    if (!ReceiptCapsule.checkForEnergyLimit(chainBaseManager.getDynamicPropertiesStore())) {
      throw new ContractValidateException(
          "contract type error, unexpected type [UpdateEnergyLimitContract]");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L97-101)
```java
    long newOriginEnergyLimit = contract.getOriginEnergyLimit();
    if (newOriginEnergyLimit <= 0) {
      throw new ContractValidateException(
          "origin energy limit must be > 0");
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/UpdateEnergyLimitContractActuatorTest.java (L229-263)
```java
  @Test
  public void twiceUpdateEnergyLimitContract() throws InvalidProtocolBufferException {
    UpdateEnergyLimitContractActuator actuator = new UpdateEnergyLimitContractActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, CONTRACT_ADDRESS, TARGET_ENERGY_LIMIT));

    UpdateEnergyLimitContractActuator secondActuator = new UpdateEnergyLimitContractActuator();
    secondActuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, CONTRACT_ADDRESS, 90L));

    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      // first
      actuator.validate();
      actuator.execute(ret);

      Assert.assertEquals(OWNER_ADDRESS,
          ByteArray.toHexString(actuator.getOwnerAddress().toByteArray()));
      Assert.assertEquals(ret.getInstance().getRet(), Protocol.Transaction.Result.code.SUCESS);
      Assert.assertEquals(
          dbManager.getContractStore().get(ByteArray.fromHexString(CONTRACT_ADDRESS))
              .getOriginEnergyLimit(), TARGET_ENERGY_LIMIT);

      // second
      secondActuator.validate();
      secondActuator.execute(ret);

      Assert.assertEquals(ret.getInstance().getRet(), Protocol.Transaction.Result.code.SUCESS);
      Assert.assertEquals(
          dbManager.getContractStore().get(ByteArray.fromHexString(CONTRACT_ADDRESS))
              .getOriginEnergyLimit(), 90L);
    } catch (ContractValidateException | ContractExeException e) {
      Assert.fail(e.getMessage());
    }
  }
```
