This is my analysis of a genuine analog in java-tron.

### Title
Contracts deployed via internal `CREATE`/`CREATE2` record the immediate creator as `originAddress`, permanently locking out `UpdateEnergyLimitContract`, `ClearABIContract`, and `UpdateSettingContract` for factory-deployed contracts - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
The Boost report's root cause is that a factory-pattern initializer permanently records the immediate caller (the factory contract) as the privileged "owner," rather than the real end user, so `onlyOwner` functions become permanently uncallable. java-tron has a structurally identical pattern: when a smart contract deploys another contract internally via `CREATE`/`CREATE2` (i.e., a factory contract deploying sub-contracts), the new contract's `originAddress` is set to the immediate caller contract, not the original external transaction sender. Several java-tron actuators (`UpdateEnergyLimitContractActuator`, `ClearABIContractActuator`, `UpdateSettingContractActuator`) treat `originAddress` as the sole "owner" credential for privileged operations and require the transaction's `owner_address` to exactly equal it.

### Finding Description
When `Program.createContractImpl` creates a new contract account, it sets the contract's `originAddress` to `senderAddress`, which is `getContextAddress()` — the address of the immediately executing contract, not the root/EOA transaction sender: [1](#0-0) 

This means if contract `Factory` internally creates contract `Child` (via `CREATE` or `CREATE2`, e.g. from a factory/proxy deployment pattern), `Child.originAddress` is permanently set to `Factory`'s address, exactly analogous to how Boost's `_makeIncentives` sets the incentive contract's `owner` to `msg.sender` (`BoostCore`).

Several java-tron actuators then gate privileged, `onlyOwner`-style operations strictly on `originAddress` matching the transaction's signer (`owner_address`):

- `UpdateEnergyLimitContractActuator.validate()` requires `ownerAddress == deployedContract.getInstance().getOriginAddress()`: [2](#0-1) 

- `ClearABIContractActuator.validate()` applies the identical check: [3](#0-2) 

- `UpdateSettingContractActuator` enforces the same "is not the owner of the contract" rule (confirmed via its test `callerNotContractOwner`): [4](#0-3) 

Because a smart contract (like `Factory`) cannot itself originate a top-level TVM/actuator transaction (it has no private key to sign `owner_address`), once `originAddress` is set to a contract address instead of an EOA, these three actuators become **permanently uncallable** for that contract — mirroring the Boost bug exactly: incorrect assignment of the privileged "owner" identity during contract creation to an intermediary contract instead of the real controlling account, resulting in stuck/unreachable owner-gated functionality.

### Impact Explanation
Any contract deployed through an on-chain factory/proxy pattern (a common and widely used deployment technique, e.g. clone factories, CREATE2 deterministic deployers) permanently loses the ability to:
- Adjust `origin_energy_limit` via `UpdateEnergyLimitContract` (this controls how much of the contract-consumed energy is paid by the contract's own energy reserve vs. the caller — an economically important resource-accounting parameter),
- Clear the contract's ABI via `ClearABIContract`,
- Update `consume_user_resource_percent` via `UpdateSettingContract` (also a resource/fee-accounting parameter).

Since these are resource-accounting/economic parameters of the deployed contract, being permanently unable to adjust them can leave a contract stuck with a suboptimal (e.g., excessively high) energy burden on its own account indefinitely, with no possible remediation path — a legitimate "stuck configuration/no-recourse" impact analogous to the reported stuck-funds scenario, though it does not directly move funds.

### Likelihood Explanation
This requires no privileged access — any regular user can deploy a factory contract that internally deploys sub-contracts via `CREATE`/`CREATE2`, which is a standard and common Solidity pattern. The condition triggers automatically as an unavoidable side effect of using `Program.createContractImpl`, requiring no special exploit steps; it is reachable by any unprivileged smart-contract developer.

### Recommendation
For contracts created via internal `CREATE`/`CREATE2`, consider propagating the root transaction's original sender (or allowing an explicit application-level "owner" designation) instead of solely recording the immediate calling contract as `originAddress`, or provide an alternative mechanism for the true deployer/owner to authorize `UpdateEnergyLimitContract`, `ClearABIContract`, and `UpdateSettingContract` operations on contracts deployed by an intermediary contract.

### Proof of Concept
1. Deploy `Factory` contract from EOA `A`.
2. `Factory` internally calls `CREATE`/`CREATE2` to deploy `Child`; per `createContractImpl`, `Child.originAddress` is set to `Factory`'s contract address (see `Program.java:852/868`).
3. Attempt to call `UpdateEnergyLimitContract`, `ClearABIContract`, or `UpdateSettingContract` on `Child` with `owner_address = A` (the actual deployer/controller).
4. Validation fails with `"Account[A] is not the owner of the contract"` because `deployedContract.getInstance().getOriginAddress()` equals `Factory`'s address, not `A`'s — and `Factory` (a contract with no private key) can never sign a transaction to satisfy the check, permanently locking these operations, as confirmed by the existing test pattern `callerNotContractOwner` in `UpdateEnergyLimitContractActuatorTest.java` and `ClearABIContractActuatorTest.java`.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L812-856)
```java
  private void createContractImpl(DataWord value, byte[] programCode, byte[] newAddress,
      boolean isCreate2) {
    byte[] senderAddress = getContextAddress();

    if (logger.isDebugEnabled()) {
      logger.debug("creating a new contract inside contract run: [{}]",
          Hex.toHexString(senderAddress));
    }

    long endowment = value.value().longValueExact();
    if (getContractState().getBalance(senderAddress) < endowment) {
      stackPushZero();
      return;
    }

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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L103-118)
```java
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
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + "] is not the owner of the contract");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ClearABIContractActuator.java (L96-111)
```java
    byte[] contractAddress = contract.getContractAddress().toByteArray();
    ContractCapsule deployedContract = contractStore.get(contractAddress);

    if (deployedContract == null) {
      throw new ContractValidateException(
          "Contract not exists");
    }

    byte[] deployedContractOwnerAddress = deployedContract.getInstance().getOriginAddress()
        .toByteArray();

    if (!Arrays.equals(ownerAddress, deployedContractOwnerAddress)) {
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + "] is not the owner of the contract");
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/UpdateSettingContractActuatorTest.java (L204-225)
```java
  @Test
  public void callerNotContractOwner() {
    UpdateSettingContractActuator actuator =
        new UpdateSettingContractActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(SECOND_ACCOUNT_ADDRESS, CONTRACT_ADDRESS, TARGET_PERCENT));

    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      actuator.validate();
      actuator.execute(ret);

      fail("Account[" + SECOND_ACCOUNT_ADDRESS + "] is not the owner of the contract");
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals(
          "Account[" + SECOND_ACCOUNT_ADDRESS + "] is not the owner of the contract",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
