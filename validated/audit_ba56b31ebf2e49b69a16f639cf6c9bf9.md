### Title
Pre-deployment configuration of `CREATE2` contract addresses allows resource/state pollution before the contract is actually initialized - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
Java-tron computes `CREATE2` contract addresses deterministically (`sha3(prefix, sender, salt, code)`), exactly like the `OnChainLab`/ERC‑6551 pattern in the external report. Because the resulting address is known in advance and any account (even a non-existent one) can be pre-created via ordinary account/resource operations (`freeze`, `delegate`), an attacker can populate account-level state at that address — before the contract is ever deployed and "initialized" there via `CREATE2`. When the contract is finally deployed, `Program.createContractImpl` only resets the account type and the delegated‑resource mapping, not all account state, so pre-existing configuration can persist into the "initialized" contract account. [1](#0-0) 

### Finding Description
`WalletUtil.generateContractAddress2` computes the `CREATE2` address purely from `(sender address, salt, code hash)`, so it is fully predictable off-chain before the contract is deployed. [1](#0-0) 

Nothing prevents a normal account from existing (or being created) at that predicted address prior to deployment. The `FreezeTest` test explicitly demonstrates this: the predicted `CREATE2` address does not exist (`assertNull`), but a `freezeForOther` call to that address implicitly creates a normal `AccountCapsule` there and sets frozen/delegated resource state on it — all before the `deploy` call that actually places contract code at that address. [2](#0-1) 

This mirrors `FreezeBalanceProcessor.validate/execute`, where a `receiverAddress` that has no existing account is silently created as a normal account and frozen-resource state is attached to it, with no dependency on whether that address is destined to later become a contract: [3](#0-2) 

When the `CREATE2` deployment eventually happens, `Program.createContractImpl` handles the case where an account already exists at the target address. Under `VMConfig.allowTvmConstantinople()`, if the account exists but is not yet a contract, the code merely flips the account type and clears the delegated-resource mapping before writing the new `SmartContract` capsule — it does not reset other account-level fields (e.g. self-frozen balances for bandwidth/energy, votes, permissions) that could have been configured on that address beforehand: [4](#0-3) 

This is the same root-cause pattern as the `OnChainLab` report: a deterministic, pre-computable account address can be configured with attacker-controlled state (frozen balance, delegated resources, and potentially other account attributes) *before* the entity that is supposed to “initialize” it (the `CREATE2` deployment transaction) actually runs — and that pre-existing configuration is not fully reset/guarded against at initialization time, only partially cleared (account type + delegated resource map).

### Impact Explanation
An attacker who predicts a future contract's `CREATE2` address can pre-load state onto that address (e.g., freeze TRX for bandwidth/energy for themselves at that address, or delegate resources to/from it) before the contract is deployed. Depending on which account fields survive the partial reset in `createContractImpl`, this could let an attacker retain frozen resources, altered resource accounting, or otherwise front-run the "trusted setup" that contract deployment is supposed to represent — an accounting/resource-integrity impact analogous to installing arbitrary validators/registries on an uninitialized `OnChainLab` wallet.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to know/predict a `CREATE2` salt+bytecode combination that some factory will later deploy, and to act (freeze/delegate) at that address beforehand — both of which are unprivileged, low-cost, and demonstrated as reachable behavior in the existing test suite (`FreezeTest.testFreezeAndUnfreezeToCreate2Contract`), i.e., no special permissions are needed to trigger this path.

### Recommendation
When an account transitions from a pre-existing normal account to a contract account in `Program.createContractImpl` (the `existingAccount != null && !contractAlreadyExists` branch), fully reset/validate all account-level state that could carry unintended configuration (frozen balances, votes, permissions), not just the account type and delegated-resource map, or reject deployment if the address has any pre-existing non-default state that has not been explicitly deemed safe to inherit.

### Proof of Concept
`framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java` (`testFreezeAndUnfreezeToCreate2Contract`, lines 372-411) already exercises this exact sequence end-to-end: it predicts a `CREATE2` address via `getCreate2Addr`, confirms the account does not exist, freezes/delegates resources to that address, and only afterward deploys the contract there via `deployCreate2Contract`, showing the address can be pre-configured before the "trusted setup" (contract deployment) occurs. [2](#0-1)

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L56-59)
```java
  public static byte[] generateContractAddress2(byte[] address, byte[] salt, byte[] code) {
    byte[] mergedData = ByteUtil.merge(address, salt, Hash.sha3(code));
    return Hash.sha3omit12(mergedData);
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java (L372-391)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L54-70)
```java
    // validate for delegating resource
    byte[] receiverAddress = param.getReceiverAddress();
    if (!FastByteComparisons.isEqual(ownerAddress, receiverAddress)) {
      param.setDelegating(true);

      // check if receiver account exists. if not, then create a new account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule == null) {
        receiverCapsule = repo.createNormalAccount(receiverAddress);
      }

      // forbid delegating resource to contract account
      if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-858)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
    Repository deposit = getContractState().newRepositoryChild();
    if (VMConfig.allowTvmConstantinople()) {
      if (existingAccount == null) {
        deposit.createAccount(newAddress, "CreatedByContract",
            AccountType.Contract);
      } else if (!contractAlreadyExists) {
        existingAccount.updateAccountType(AccountType.Contract);
        existingAccount.clearDelegatedResource();
        deposit.updateAccount(newAddress, existingAccount);
      }

      if (!contractAlreadyExists) {
        Builder builder = SmartContract.newBuilder();
        if (VMConfig.allowTvmCompatibleEvm()) {
          builder.setVersion(getContractVersion());
        }
        builder.setContractAddress(ByteString.copyFrom(newAddress))
            .setConsumeUserResourcePercent(100)
            .setOriginAddress(ByteString.copyFrom(senderAddress));
        if (isCreate2) {
          builder.setTrxHash(ByteString.copyFrom(rootTransactionId));
        }
        SmartContract newSmartContract = builder.build();
        deposit.createContract(newAddress, new ContractCapsule(newSmartContract));
      }
```
