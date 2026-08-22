## Title
Front-runnable CREATE2 address squatting can permanently DOS internal contract deployment - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

## Summary
`Program.createContractImpl()` blocks contract creation whenever an account/contract is already found at the deterministically-computed target address, mirroring the reported bug class where anyone able to pre-populate a permissionless, deterministically-addressed state slot can permanently gate a legitimate on-chain "create" operation and DOS everything downstream of it.

## Finding Description
When a smart contract executes `CREATE2`, the target address is fully deterministic and publicly computable ahead of time from `(sender, salt, keccak256(initcode))` [1](#0-0) . In `createContractImpl`, the VM looks up whatever account may already exist at that address and, prior to the Constantinople hard fork flag, treats *any* pre-existing account (e.g. one created by a plain TRX transfer, or via `freeze`/`delegate` on a not-yet-deployed address, as demonstrated in the test suite) as `contractAlreadyExists = true`, aborting the deployment with `BytecodeExecutionException("Trying to create a contract with existing contract address...")`: [2](#0-1) [3](#0-2) 

This is functionally identical in shape to the reported issue: a permissionless, anyone-can-call action (create pair / create account state) that populates a deterministic key before the "legitimate" privileged flow gets to run, permanently tripping an "already exists → revert" guard and DOSing everything gated behind successful creation (in the TVM case: the deployed contract and any logic that depends on it being deployed at that exact predicted address, such as counterfactual-instantiation patterns, vanity/factory deployments, or state-channel/wallet designs that rely on CREATE2 predictability).

Java-tron does mitigate this for the common case once `allowTvmConstantinople` is active by additionally requiring `isContractExist(existingAccount, ...)` (i.e. code must actually be present) before treating the slot as occupied [4](#0-3) , and the `FreezeTest.testFreezeAndUnfreezeToCreate2Contract` test confirms that freezing/delegating TRX to the predicted address does not block a subsequent legitimate deployment [5](#0-4) . However, an attacker who can front-run and *actually deploy real bytecode* (any bytecode, via a race using the same predictable salt/initcode-hash) at the target address before the legitimate factory transaction lands will still cause `isContractExist` to be true, permanently blocking the intended deployment at that address for the life of the chain — there is no mechanism analogous to "only the trusted factory may claim this CREATE2 slot."

## Impact Explanation
Any contract or application built on TVM that relies on CREATE2 for deterministic, permissionless-verifiable deployment (factories, counterfactual wallets, escrow/pair-style contracts) can have their intended contract address permanently squatted by a third party who observes the pending deployment transaction (mempool) or independently knows the salt/initcode. Once squatted, the legitimate deployment transaction will always revert with `BytecodeExecutionException`, permanently denying the application's create flow — directly analogous to the LaunchEvent unable to ever obtain a valid `pair`, freezing `withdrawLiquidity()`/`withdrawIncentives()` forever. This is a protocol-level DoS reachable purely through TVM execution of ordinary broadcast transactions (`TriggerSmartContract`), not requiring any privileged actor.

## Likelihood Explanation
Likelihood is bounded by two factors that keep this below a high-severity finding: (1) exploitation requires the attacker to predict or observe the exact `(sender, salt, initcode)` triple before the legitimate deployer's transaction is confirmed, which usually means mempool front-running or knowledge of an off-chain agreed salt; (2) since `allowTvmConstantinople`, only genuine bytecode occupation (not simple balance/freeze operations) trips the guard, narrowing the practical exploitation to a genuine deploy-race rather than a cheap "send 1 sun" attack. This mirrors the acknowledged-but-unfixed nature of the original finding: the underlying primitive (deterministic, permissionless address claiming) is inherent to the CREATE2 opcode semantics themselves, not a bug unique to a specific java-tron actuator, so the root cause cannot be fully closed within `Program.java` alone.

## Recommendation
- Document clearly (and warn dApp developers) that CREATE2 addresses on TVM are front-runnable and that any protocol depending on a specific address containing intended bytecode must not assume this address is uncontested; protocols should use commit-reveal or reserve-then-deploy patterns instead of bare CREATE2.
- Consider hardening `createContractImpl` so that when `contractAlreadyExists` is true because of attacker-deployed bytecode, the *deployer* (not just the transaction) can be given a way to detect the squat pre-flight (e.g., expose a precompile/opcode to check "will this CREATE2 succeed" prior to committing value/energy), reducing wasted energy from a doomed deployment.
- No change is required to the Constantinople-era balance/freeze distinguishing logic, which already correctly avoids treating simple funded accounts as occupied.

## Proof of Concept
1. Attacker observes (via mempool or shared off-chain salt convention) a pending `TriggerSmartContract` transaction that will execute `CREATE2(salt, initcode)` from a known factory contract address `F`.
2. Attacker computes the deterministic target address `A = keccak256(0x41 ‖ F ‖ salt ‖ keccak256(initcode))[12:]` using the same formula implemented in `WalletUtil.generateContractAddress2` [1](#0-0) .
3. Attacker submits their own `CREATE2` (or `CREATE` landing at the same address via a colliding path) with arbitrary bytecode targeting address `A`, ahead of the victim's transaction, so that `getContractState().getAccount(A)` returns a genuine contract capsule.
4. When the victim's original transaction executes, `createContractImpl` computes `contractAlreadyExists = true` (since `isContractExist` returns true for genuine deployed code) and sets `BytecodeExecutionException("Trying to create a contract with existing contract address: 0x" + A)` [6](#0-5) , permanently preventing the victim's factory from ever deploying its intended contract at `A`.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L56-59)
```java
  public static byte[] generateContractAddress2(byte[] address, byte[] salt, byte[] code) {
    byte[] mergedData = ByteUtil.merge(address, salt, Hash.sha3(code));
    return Hash.sha3omit12(mergedData);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-833)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L905-910)
```java
    ProgramResult createResult = ProgramResult.createEmpty();

    if (contractAlreadyExists) {
      createResult.setException(new BytecodeExecutionException(
          "Trying to create a contract with existing contract address: 0x" + Hex
              .toHexString(newAddress)));
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
