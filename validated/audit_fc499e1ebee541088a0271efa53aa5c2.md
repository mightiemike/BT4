### Title
Contract owner can front-run pending calls by instantly raising `consume_user_resource_percent` to 100%, forcing callers to unexpectedly pay for all energy - (File: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java`)

### Summary
This is the same bug class as the Maia `BoostAggregator` finding: an account that controls a shared, publicly-usable resource (a smart contract) can unilaterally and instantly change a fee-split parameter that other, unrelated users are relying on, and that change is applied retroactively to already-pending/in-flight interactions instead of only to future ones.

### Finding Description
`UpdateSettingContract` lets the contract owner set `consume_user_resource_percent`, which decides what fraction of the energy bill for a `TriggerSmartContract` call is charged to the caller vs. the contract's `origin`/creator account. [1](#0-0) 

The only checks are that the percent is within `[0,100]` and that the caller owns the contract - there is no rate limit, cooldown, or delayed activation: [2](#0-1) 

When any user's `TriggerSmartContract` transaction later executes, the energy split is computed using whatever `consume_user_resource_percent` is stored **at execution time**, not at the time the caller signed/submitted their transaction: [3](#0-2) [4](#0-3) 

By contrast, java-tron already recognizes this exact bug class elsewhere and mitigates it: `UpdateBrokerageContract` (a witness/SR changing its reward commission) is deliberately staged so a change only takes effect starting the *next* voting cycle, never affecting rewards already accrued in the current cycle: [5](#0-4) [6](#0-5) 

`UpdateSettingContractActuator` has no equivalent staging - the same "owner can change the fee split that other people rely on" pattern that the Maia report flagged is present here without the mitigation TRON itself applied to the brokerage case.

### Impact Explanation
A contract deployer can publish a contract advertising a low `consume_user_resource_percent` (e.g. 0%, "the deployer pays all energy") to attract users. Once users begin submitting `TriggerSmartContract` calls expecting to pay little or nothing, the owner submits an `UpdateSettingContract` transaction (a normal, cheap transaction, `calcFee()` is not even charged beyond bandwidth) setting the percent to 100%. Any caller transaction that executes afterward, including ones already broadcast and sitting in the mempool, is charged full energy cost from the caller's own frozen/staked energy or TRX, instead of the amount the business logic promised - an unexpected, uncapped loss of the caller's resources/TRX with no way for the caller to detect or prevent it in advance. This is an accounting/resource-corruption issue reachable purely through normal broadcast transactions from an unprivileged (with respect to the wider network) contract owner.

### Likelihood Explanation
Likelihood is Medium: it requires a malicious or compromised contract owner (comparable trust assumption to a `BoostAggregator` deployer in the referenced report — anyone can deploy a TRON smart contract and is not otherwise privileged), but no other precondition (no governance/committee, no P2P/node compromise) is needed, and the transaction type is trivially reachable via `/wallet/updatesetting` HTTP endpoint or gRPC `UpdateSettingContract`. [7](#0-6) 

### Recommendation
Apply the same cycle/height-delayed activation pattern already used for `UpdateBrokerageContract`: store pending `consume_user_resource_percent` updates separately and only apply them starting from a future block/maintenance boundary, so in-flight or already-broadcast caller transactions are billed using the percent that was in effect when they were created, not one that was changed afterward by the contract owner.

### Proof of Concept
1. Deploy contract `C` with `consume_user_resource_percent = 0` (deployer pays all energy).
2. Wait for a victim to sign and broadcast `TriggerSmartContract` against `C`.
3. Before that transaction is packed into a block, broadcast `UpdateSettingContract{contract_address: C, consume_user_resource_percent: 100}` from the owner account.
4. When the victim's transaction executes, `TransactionTrace.pay()` reads the now-updated `consumeUserResourcePercent` value of 100 and bills the victim's account for the full energy usage instead of 0, per [3](#0-2) , causing an unexpected TRX/energy loss for the victim.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L31-49)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) object;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    ContractStore contractStore = chainBaseManager.getContractStore();
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

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L93-113)
```java
    long newPercent = contract.getConsumeUserResourcePercent();
    if (newPercent > ActuatorConstant.ONE_HUNDRED || newPercent < 0) {
      throw new ContractValidateException(
          "percent not in [0, 100]");
    }

    byte[] contractAddress = contract.getContractAddress().toByteArray();
    ContractCapsule deployedContract = contractStore.get(contractAddress);

    if (deployedContract == null) {
      throw new ContractValidateException(
          "Contract does not exist");
    }

    byte[] deployedContractOwnerAddress = deployedContract.getInstance().getOriginAddress()
        .toByteArray();

    if (!Arrays.equals(ownerAddress, deployedContractOwnerAddress)) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] is not the owner of the contract");
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

**File:** framework/src/test/java/org/tron/common/utils/client/utils/HttpMethed.java (L2919-2945)
```java
  /** constructor. */
  public static HttpResponse updateSetting(
      String httpNode,
      byte[] ownerAddress,
      String contractAddress,
      Integer consumeUserResourcePercent,
      String fromKey) {
    try {
      final String requestUrl = "http://" + httpNode + "/wallet/updatesetting";
      JsonObject userBaseObj2 = new JsonObject();
      userBaseObj2.addProperty("owner_address", ByteArray.toHexString(ownerAddress));
      userBaseObj2.addProperty("contract_address", contractAddress);
      userBaseObj2.addProperty("consume_user_resource_percent", consumeUserResourcePercent);
      logger.info(userBaseObj2.toString());
      response = createConnect(requestUrl, userBaseObj2);
      transactionString = EntityUtils.toString(response.getEntity());
      transactionSignString = gettransactionsign(httpNode, transactionString, fromKey);
      logger.info(transactionString);
      logger.info(transactionSignString);
      response = broadcastTransaction(httpNode, transactionSignString);
    } catch (Exception e) {
      e.printStackTrace();
      httppost.releaseConnection();
      return null;
    }
    return response;
  }
```
