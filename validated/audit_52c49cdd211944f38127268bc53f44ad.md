Based on the codebase, the closest concrete analog to the reported bug class (front-running an attacker-supplied identifier to force a victim's "create" operation to collide and revert, causing fee loss) is the **CREATE2 address-collision griefing** in java-tron's TVM execution.

### Title
Denial-of-Service via CREATE2 Address Front-Running in TVM Contract Creation - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
Just like the Folks Finance `LoanManager` allows a user to pick a `loanId` and reverts if that ID is already active (enabling an attacker to squat the ID first), java-tron's `CREATE2` opcode handling lets a caller derive a target contract address deterministically from `salt` + `programCode`, then reverts the create if that address is already occupied. An attacker who observes a pending `TriggerSmartContract` transaction calling a `CREATE2` factory with a specific `salt`/init-code can front-run it with the identical `salt`/init-code, occupying the target address first and causing the victim's transaction to fail while consuming victim energy/fees, exactly mirroring the "same-ID front-run causes revert + fee loss" pattern in the report.

### Finding Description
`Program.createContractImpl()` computes `newAddress` deterministically (for `CREATE2`, from sender+salt+code hash) and checks whether an account already exists there: [1](#0-0) 

Crucially, energy for the create is spent unconditionally *before* the collision result is known: [2](#0-1) 

If `contractAlreadyExists` is true, the created call fails with a `BytecodeExecutionException` ("Trying to create a contract with existing contract address"), and internal transactions are rejected: [3](#0-2) 

This scenario is already explicitly exercised in the test suite, where a second caller triggering the same factory `deploy(code, salt)` with the same salt fails with an `OutOfEnergyException` after the first caller's identical call succeeds: [4](#0-3) 

The top-level `CREATE` (contract-deployment transaction) path has an analogous, though less front-runnable, check: [5](#0-4) 

### Impact Explanation
Any anonymous account can front-run a pending `TriggerSmartContract` transaction that invokes a `CREATE2`-based factory (a common on-chain pattern, e.g., counterfactual deployments, deterministic wallet/vault deployers) by resubmitting the identical `salt` and init-code with a higher energy/bandwidth price. Because the target address is fully deterministic and salt/code are visible in the mempool, the attacker can guarantee the collision. The victim's transaction executes up to the `CREATE2` call, is charged the create-overhead energy plus all prior opcode energy, and then fails — a real, repeatable resource/fee loss and griefing vector against any protocol relying on `CREATE2` for deterministic deployment on java-tron.

### Likelihood Explanation
High for any dApp using `CREATE2` factories with attacker-observable (mempool-visible) `salt`/init-code — this requires no special privileges, only the ability to broadcast a `TriggerSmartContract` transaction with sufficient fee/energy to be included first, identical to the "monitor and front-run" technique described in the original report.

### Recommendation
Where feasible, application-level factories should bind `CREATE2` salts to `msg.sender` (a standard mitigation) so an attacker cannot reuse a victim's exact salt to claim the same address; this is a contract-level fix rather than a protocol-level one, since `CREATE2`'s address-collision-revert semantics are consensus-critical and match the EVM specification. At the protocol level, java-tron could optionally document/warn integrators about the front-runnable nature of `CREATE2`, but changing the collision-detection behavior itself in `Program.createContractImpl` would break EVM compatibility and consensus with existing chain history.

### Proof of Concept
1. Deploy a `Factory` contract exposing `deploy(bytes code, uint256 salt)` that internally executes `create2(0, add(code,0x20), mload(code), salt)`, as in `Create2Test.java`.
2. Victim broadcasts a `TriggerSmartContract` transaction calling `factory.deploy(testCode, salt)`.
3. Attacker observes the pending transaction, extracts `testCode` and `salt`, and broadcasts `factory.deploy(testCode, salt)` themselves with a higher fee so it is packed first, per `Create2Test.java` lines 337-347.
4. Attacker's transaction succeeds, creating the contract at the deterministic address.
5. Victim's transaction executes the same `CREATE2`; `Program.createContractImpl` finds `contractAlreadyExists == true`, spends the create energy, and raises `BytecodeExecutionException`, resulting in the victim's call reverting/failing (`OutOfEnergyException` observed in the test) while resources were already consumed. [6](#0-5)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-833)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L888-890)
```java
    // actual energy subtract
    DataWord energyLimit = this.getCreateEnergy(getEnergyLimitLeft());
    spendEnergy(energyLimit.longValue(), "internal call");
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L905-910)
```java
    ProgramResult createResult = ProgramResult.createEmpty();

    if (contractAlreadyExists) {
      createResult.setException(new BytecodeExecutionException(
          "Trying to create a contract with existing contract address: 0x" + Hex
              .toHexString(newAddress)));
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/Create2Test.java (L311-351)
```java
    TVMTestResult result = TvmTestUtils
        .triggerContractAndReturnTvmTestResult(Hex.decode(OWNER_ADDRESS),
            factoryAddress, Hex.decode(hexInput), 0, fee, manager, null);
    Assert.assertNull(result.getRuntime().getRuntimeError());

    byte[] returnValue = result.getRuntime().getResult().getHReturn();
    byte[] actualContract = convertToTronAddress(Arrays.copyOfRange(returnValue,
        12, 32));
    byte[] expectedContract =
        generateContractAddress2(factoryAddress,
            new DataWord(salt).getData(), Hex.decode(testCode));
    // check deployed contract
    Assert.assertEquals(actualContract, expectedContract);

    // trigger get function in smart contract and compare the actual
    // contract address with the value
    // computed in contract
    String methodToTrigger = "get(bytes1,bytes,uint256)";
    hexInput = AbiUtil.parseMethod(methodToTrigger,
        Arrays.asList(Wallet.getAddressPreFixString(), testCode, salt));
    // same input
    result = TvmTestUtils.triggerContractAndReturnTvmTestResult(Hex.decode(OWNER_ADDRESS),
              factoryAddress, Hex.decode(hexInput), 0, fee, manager, null);
    Assert.assertEquals(result.getRuntime().getResult().getHReturn(),
          new DataWord(new DataWord(actualContract).getLast20Bytes()).getData());

    String ownerAddress2 = Wallet.getAddressPreFixString()
        + "8dcd6d3b585e41863123af20e57ec9f678035d92";
    rootDeposit.createAccount(Hex.decode(ownerAddress2), AccountType.Normal);
    rootDeposit.addBalance(Hex.decode(ownerAddress2), 30000000000000L);
    rootDeposit.commit();

    // deploy contract by OTHER user again, should fail
    hexInput = AbiUtil.parseMethod(methodDeploy, Arrays.asList(testCode, salt));
    result = TvmTestUtils
        .triggerContractAndReturnTvmTestResult(Hex.decode(ownerAddress2),
            factoryAddress, Hex.decode(hexInput), 0, fee, manager, null);
    Assert.assertNotNull(result.getRuntime().getRuntimeError());
    Assert.assertTrue(result.getRuntime().getResult().getException()
        instanceof OutOfEnergyException);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L355-361)
```java
    byte[] contractAddress = WalletUtil.generateContractAddress(trx);
    // insure the new contract address haven't exist
    if (rootRepository.getAccount(contractAddress) != null) {
      throw new ContractValidateException(
          "Trying to create a contract with existing contract address: " + StringUtil
              .encode58Check(contractAddress));
    }
```
