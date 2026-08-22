## Title
Innocent contract callers can incur unexpected accounting losses via front-run `UpdateSettingContract`/`UpdateEnergyLimitContract` changes to energy cost-sharing ratio - (File: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java`)

### Summary
A smart-contract owner (an ordinary, unprivileged account — not a system/committee role) can call `UpdateSettingContract` to instantly change `consume_user_resource_percent` for their deployed contract, and this new value takes effect immediately and is read live at both TVM execution time and fee-settlement time. There is no per-transaction pinning or delay mechanism, so the owner can front-run a pending `TriggerSmartContract` from any caller and shift the energy-fee split away from the origin account and onto the unsuspecting caller, causing the caller to be billed a larger, unexpected share of the energy cost. This mirrors the reported Lavarage bug where a lender front-runs a borrower's pending transaction by instantly raising the interest rate stored at the pool level.

### Finding Description
`UpdateSettingContractActuator.execute` writes the new `consume_user_resource_percent` directly into the `ContractStore` with no cooldown, no minimum-notice period, and no linkage to a future cycle: [1](#0-0) 

`UpdateEnergyLimitContractActuator` behaves the same way for `origin_energy_limit`: [2](#0-1) 

At TVM execution time, `VMActuator.getTotalEnergyLimitWithFixRatio` reads the *current* `consumeUserResourcePercent` from the `ContractCapsule` to compute how much energy the caller vs. the contract owner can spend: [3](#0-2) 

At settlement/billing time, `TransactionTrace.pay()` again re-reads the live percentage from the `ContractStore` (not a value captured when the caller signed/broadcast their transaction) to determine what fraction of the actually-consumed energy each party pays: [4](#0-3) 

`ReceiptCapsule.payEnergyBill` then splits the actual energy usage between `origin` and `caller` using that freshly-read percent, debiting whichever side ends up responsible: [5](#0-4) 

Because `consume_user_resource_percent` is stored at the contract level (analogous to the lending report's "interest rate stored at the trading pool level") and is applied at execution time rather than pinned to the state seen when the caller signed their `TriggerSmartContract`, a contract owner can broadcast `UpdateSettingContract(percent=100)` immediately before a victim's pending `TriggerSmartContract` is packed into a block. If it lands first (which an attacker/validator can arrange since transaction ordering within a block is not guaranteed to match submission order), the victim's call is billed under the new 100% ratio instead of whatever ratio (e.g. 30%) the caller expected when composing/signing their transaction.

By contrast, java-tron's own `UpdateBrokerageContract`/`DelegationStore` mechanism deliberately defers a similarly "counterparty-controlled ratio" change to the *next* voting cycle specifically to avoid this kind of front-running: [6](#0-5) [7](#0-6) 
No equivalent delay exists for `consume_user_resource_percent`/`origin_energy_limit`, so the same class of issue the report describes is present here without the mitigation java-tron already applies elsewhere.

### Impact Explanation
A caller invoking a smart contract has no way to guarantee what fraction of energy cost they will actually be billed for at execution time — the value can be changed by the contract owner between the moment the caller signs/broadcasts their transaction and the moment it executes. This can cause the caller's account balance to be debited for energy fees (up to their `fee_limit`) that they did not expect to pay, i.e., an unexpected, non-consensual funds/accounting loss for an ordinary unprivileged caller, driven entirely by another unprivileged account's action. There is no field in `TriggerSmartContract` allowing the caller to specify a maximum acceptable `consume_user_resource_percent`, so callers have no on-chain protection mechanism analogous to a "max interest rate" parameter.

### Likelihood Explanation
Any account that deploys or owns a smart contract can call `UpdateSettingContract`/`UpdateEnergyLimitContract` at will — this requires no special/consensus privilege, matching the "unprivileged actor" bar. Front-running within the same/adjacent block is a normal capability in blockchain systems (as acknowledged in the source report for Solana, and applicable analogously to block producers/attackers on java-tron), and the actuator provides no cooldown, delay, or snapshot-at-submission-time protection, unlike the brokerage-rate case which was explicitly hardened against this exact pattern. This makes the issue readily reachable via the normal `TriggerSmartContract` broadcast path used by every dApp caller.

### Recommendation
- Snapshot `consume_user_resource_percent` (and `origin_energy_limit`) usage for fee-splitting either at the point the transaction enters the block (not re-read at settlement) or apply changes only starting from the next block/cycle, similar to the deferred-cycle approach already used for `UpdateBrokerageContract`.
- Alternatively, add an optional caller-specified bound (e.g., a max acceptable self-pay percentage or minimum expected origin contribution) to `TriggerSmartContract`, validated against the live `consume_user_resource_percent` at execution time, and reject the transaction if the on-chain value is less favorable than the caller specified.

### Proof of Concept
1. Contract owner deploys a contract with `consume_user_resource_percent = 0` (caller pays nothing, origin absorbs all energy cost from frozen balance) — this is the value callers observe on-chain and rely on when signing calls.
2. A user submits `TriggerSmartContract` expecting the existing 0% split to apply.
3. Before the user's transaction is included, the contract owner submits `UpdateSettingContract{contract_address, consume_user_resource_percent=100}` (see `UpdateSettingContractActuatorTest.successUpdateSettingContract`/`twiceUpdateSettingContract` showing the value is mutable at will and takes effect on the very next transaction against the contract): [8](#0-7) 
4. If the owner's update transaction is ordered before the victim's `TriggerSmartContract` in the same or an earlier block, `VMActuator.getTotalEnergyLimitWithFixRatio` and `TransactionTrace.pay()`/`ReceiptCapsule.payEnergyBill` will use `consume_user_resource_percent = 100`, causing the caller to be billed for 100% of the energy usage instead of the 0% they expected, up to their `fee_limit` — an unexpected loss with no possibility for the caller to have protected against it in-transaction.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L40-49)
```java
    try {
      UpdateSettingContract usContract = any.unpack(UpdateSettingContract.class);
      long newPercent = usContract.getConsumeUserResourcePercent();
      byte[] contractAddress = usContract.getContractAddress().toByteArray();
      ContractCapsule deployedContract = contractStore.get(contractAddress);

      contractStore.put(contractAddress, new ContractCapsule(
          deployedContract.getInstance().toBuilder().setConsumeUserResourcePercent(newPercent)
              .build()));
      RepositoryImpl.removeLruCache(contractAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L39-49)
```java
    try {
      UpdateEnergyLimitContract usContract = any.unpack(UpdateEnergyLimitContract.class);
      long newOriginEnergyLimit = usContract.getOriginEnergyLimit();
      byte[] contractAddress = usContract.getContractAddress().toByteArray();
      ContractCapsule deployedContract = contractStore.get(contractAddress);

      contractStore.put(contractAddress, new ContractCapsule(
          deployedContract.getInstance().toBuilder().setOriginEnergyLimit(newOriginEnergyLimit)
              .build()));
      RepositoryImpl.removeLruCache(contractAddress);

```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L724-757)
```java
    long creatorEnergyLimit = 0;
    ContractCapsule contractCapsule = rootRepository
        .getContract(contract.getContractAddress().toByteArray());
    long consumeUserResourcePercent = contractCapsule.getConsumeUserResourcePercent(
        VMConfig.disableJavaLangMath());

    long originEnergyLimit = contractCapsule.getOriginEnergyLimit();
    if (originEnergyLimit < 0) {
      throw new ContractValidateException("originEnergyLimit can't be < 0");
    }

    long originEnergyLeft = 0;
    if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
      originEnergyLeft = rootRepository.getAccountLeftEnergyFromFreeze(creator);
      if (VMConfig.allowTvmFreeze() || VMConfig.allowTvmFreezeV2()) {
        receipt.setOriginEnergyLeft(originEnergyLeft);
      }
    }
    if (consumeUserResourcePercent <= 0) {
      creatorEnergyLimit = min(originEnergyLeft, originEnergyLimit,
          VMConfig.disableJavaLangMath());
    } else {
      if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
        // creatorEnergyLimit =
        // min(callerEnergyLimit * (100 - percent) / percent,
        //   creatorLeftFrozenEnergy, originEnergyLimit)

        creatorEnergyLimit = min(
            BigInteger.valueOf(callerEnergyLimit)
                .multiply(BigInteger.valueOf(VMConstant.ONE_HUNDRED - consumeUserResourcePercent))
                .divide(BigInteger.valueOf(consumeUserResourcePercent)).longValueExact(),
            min(originEnergyLeft, originEnergyLimit, VMConfig.disableJavaLangMath()),
            VMConfig.disableJavaLangMath());
      }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L239-253)
```java
      case TRX_CONTRACT_CALL_TYPE:
        TriggerSmartContract callContract = ContractCapsule
            .getTriggerContractFromTransaction(trx.getInstance());
        ContractCapsule contractCapsule =
            contractStore.get(callContract.getContractAddress().toByteArray());

        callerAccount = callContract.getOwnerAddress().toByteArray();
        originAccount = contractCapsule.getOriginAddress();
        boolean disableJavaLangMath = dynamicPropertiesStore.disableJavaLangMath();
        percent = max(Constant.ONE_HUNDRED - contractCapsule.getConsumeUserResourcePercent(
            disableJavaLangMath), 0, disableJavaLangMath);
        percent = min(percent, Constant.ONE_HUNDRED,
            disableJavaLangMath);
        originEnergyLimit = contractCapsule.getOriginEnergyLimit();
        break;
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java (L201-238)
```java
  public void payEnergyBill(DynamicPropertiesStore dynamicPropertiesStore,
      AccountStore accountStore, ForkController forkController, AccountCapsule origin,
      AccountCapsule caller,
      long percent, long originEnergyLimit, EnergyProcessor energyProcessor, long now)
      throws BalanceInsufficientException {

    // Reset origin energy usage here! Because after stake 2.0, this field are reused for
    // recording pre-merge frozen energy for origin account. If total energy usage is zero, this
    // field will be a dirty record.
    this.setOriginEnergyUsage(0);

    if (receipt.getEnergyUsageTotal() <= 0) {
      return;
    }

    if (Objects.isNull(origin) && dynamicPropertiesStore.getAllowTvmConstantinople() == 1) {
      payEnergyBill(dynamicPropertiesStore, accountStore, forkController, caller,
          receipt.getEnergyUsageTotal(), receipt.getResult(), energyProcessor, now);
      return;
    }
    boolean disableJavaLangMath = dynamicPropertiesStore.disableJavaLangMath();

    if ((!Objects.isNull(origin)) && caller.getAddress().equals(origin.getAddress())) {
      payEnergyBill(dynamicPropertiesStore, accountStore, forkController, caller,
          receipt.getEnergyUsageTotal(), receipt.getResult(), energyProcessor, now);
    } else {
      long originUsage = multiplyExact(receipt.getEnergyUsageTotal(), percent, disableJavaLangMath)
          / 100;
      originUsage = getOriginUsage(dynamicPropertiesStore, origin, originEnergyLimit,
          energyProcessor,
          originUsage);

      long callerUsage = receipt.getEnergyUsageTotal() - originUsage;
      energyProcessor.useEnergy(origin, originUsage, now);
      this.setOriginEnergyUsage(originUsage);
      payEnergyBill(dynamicPropertiesStore, accountStore, forkController,
          caller, callerUsage, receipt.getResult(), energyProcessor, now);
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L99-118)
```java
  public void setBrokerage(long cycle, byte[] address, int brokerage) {
    put(buildBrokerageKey(cycle, address), new BytesCapsule(ByteArray.fromInt(brokerage)));
  }

  public int getBrokerage(long cycle, byte[] address) {
    BytesCapsule bytesCapsule = get(buildBrokerageKey(cycle, address));
    if (bytesCapsule == null) {
      return DEFAULT_BROKERAGE;
    } else {
      return ByteArray.toInt(bytesCapsule.getData());
    }
  }

  public void setBrokerage(byte[] address, int brokerage) {
    setBrokerage(-1, address, brokerage);
  }

  public int getBrokerage(byte[] address) {
    return getBrokerage(-1, address);
  }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L154-162)
```java
    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/UpdateSettingContractActuatorTest.java (L227-259)
```java
  @Test
  public void twiceUpdateSettingContract() {
    UpdateSettingContractActuator actuator =
        new UpdateSettingContractActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, CONTRACT_ADDRESS, TARGET_PERCENT));

    UpdateSettingContractActuator secondActuator =
        new UpdateSettingContractActuator();
    secondActuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, CONTRACT_ADDRESS, 90L));

    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      // first
      actuator.validate();
      actuator.execute(ret);

      Assert.assertEquals(ret.getInstance().getRet(), Protocol.Transaction.Result.code.SUCESS);
      Assert.assertEquals(
          dbManager.getContractStore().get(ByteArray.fromHexString(CONTRACT_ADDRESS))
              .getConsumeUserResourcePercent(
                  dbManager.getDynamicPropertiesStore().disableJavaLangMath()), TARGET_PERCENT);

      // second
      secondActuator.validate();
      secondActuator.execute(ret);

      Assert.assertEquals(ret.getInstance().getRet(), Protocol.Transaction.Result.code.SUCESS);
      Assert.assertEquals(
          dbManager.getContractStore().get(ByteArray.fromHexString(CONTRACT_ADDRESS))
              .getConsumeUserResourcePercent(
                  dbManager.getDynamicPropertiesStore().disableJavaLangMath()), 90L);
```
