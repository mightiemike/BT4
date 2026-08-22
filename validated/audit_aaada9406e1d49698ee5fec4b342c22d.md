### Title
DoS of CREATE2 contract deployment via pre-occupation of the deterministic address - (`File: actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
The reported issue is a Solana/Anchor pattern where a deterministically-derived account (an associated token account, whose address is derived only from `mint` + `authority`) is declared with `init`, so any attacker who pre-creates that same address before the legitimate transaction can permanently block the intended creation. The same failure mode exists in java-tron's TVM `CREATE2` implementation: an unprivileged caller can pre-occupy a deterministically computed contract address, permanently blocking a specific legitimate deployment that targets the same address.

### Finding Description
`CREATE2` addresses in java-tron are computed deterministically from `sender ++ salt ++ keccak256(code)` via `WalletUtil.generateContractAddress2` [1](#0-0) , exactly analogous to a Solana PDA/ATA address that is fully determined by its seeds (`mint`/`authority` in the report).

When a contract executes `CREATE2` (or `CREATE`), `Program.createContractImpl` is invoked. It looks up whether an account/contract already exists at the computed `newAddress`: [2](#0-1) 

If a contract already exists at that address (`contractAlreadyExists == true`), the account/contract creation steps are skipped, and — critically — the operation is made to fail unconditionally with a `BytecodeExecutionException`: [3](#0-2) 

Because both `salt` and `code` (and therefore the resulting `keccak256(code)`) are attacker-controllable inputs passed as ordinary calldata to a public factory function (as demonstrated by the `Factory.deploy(bytes code, uint256 salt)` pattern used in the project's own tests) [4](#0-3) , any unprivileged account can observe a pending "deploy with salt S and code C" transaction in the mempool, and submit its own call to the same factory with the same `salt`/`code` first. This lets the attacker's `CREATE2` be included first, permanently occupying the deterministic address for that (`sender`, `salt`, `code`) tuple. The legitimate deployer's later transaction to the same address will always hit `contractAlreadyExists == true` and be reverted — there is no fallback such as "reuse existing/init-if-needed" logic for the `CREATE2` collision case; the deployment for that specific salt is permanently and irrecoverably blocked (unlike the ordinary account-conversion path taken when a *plain* funded account, not yet a contract, occupies the address, which is handled gracefully at lines 836-843 of the same file).

This mirrors the report's root cause precisely: a resource whose address/identity is fully deterministic from public inputs is created with unconditional "must not already exist" semantics, allowing griefing/front-running by any unprivileged actor.

### Impact Explanation
An attacker can permanently deny a specific `CREATE2` deployment (a specific `salt`+`code` combination) intended by any user or dApp factory contract on-chain, by racing a nearly-identical transaction ahead of the victim's. This is a targeted, on-chain, unprivileged DoS against TVM contract-creation execution, reachable from any broadcast transaction/contract call, consistent with a Medium-severity classification as in the original report.

### Likelihood Explanation
Exploitation requires observing the intended `salt`/`code` pair before it is finalized on-chain (e.g., via mempool observation or a predictable/fixed salt scheme used by a factory) and submitting a competing transaction with sufficient priority to land first — a standard front-running primitive already assumed feasible for blockchain networks. Given `CREATE2` factories are a common pattern (as evidenced by the project's own `Create2Test`/`FreezeTest` fixtures), the precondition (a factory whose `deploy` function is called with attacker-visible parameters) is realistic.

### Recommendation
- For `CREATE2` deployments where idempotent semantics are desired, consider allowing the deployer to detect/reuse an existing deployment at the same address (analogous to `init_if_needed`) when the existing contract's code matches the intended `initcode`, rather than unconditionally reverting.
- Alternatively/additionally, document and encourage factory-contract designs that bind `salt` to `msg.sender` or another caller-specific value so the deterministic address cannot be trivially pre-computed and squatted by a third party (mitigation at the application layer), and consider adding guidance/warnings in TVM documentation about this front-running risk for `CREATE2`.

### Proof of Concept
1. A factory contract `F` (like the one in `Create2Test.java`) exposes `deploy(bytes code, uint256 salt)` which internally executes `CREATE2` using `msg.sender`-independent salt.
2. Victim broadcasts `F.deploy(codeV, saltX)`, intending to deploy at address `A = keccak256(prefix, F, saltX, keccak256(codeV))`.
3. Attacker observes this pending transaction, and broadcasts `F.deploy(codeV, saltX)` (or any code, same salt, whose hash also collides is unnecessary — using the exact same code/salt suffices) with higher fee/earlier inclusion.
4. Attacker's transaction executes first; `Program.createContractImpl` creates the contract at `A` [5](#0-4) .
5. Victim's transaction now finds `contractAlreadyExists == true` for address `A`, and unconditionally fails with `BytecodeExecutionException("Trying to create a contract with existing contract address...")` [3](#0-2) , permanently denying the victim's intended deployment for that salt.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L55-59)
```java
  // for `CREATE2`
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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L845-858)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L905-911)
```java
    ProgramResult createResult = ProgramResult.createEmpty();

    if (contractAlreadyExists) {
      createResult.setException(new BytecodeExecutionException(
          "Trying to create a contract with existing contract address: 0x" + Hex
              .toHexString(newAddress)));
    } else if (isNotEmpty(programCode)) {
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/Create2Test.java (L34-47)
```java
  pragma solidity 0.5.0;
  contract Factory {
      event Deployed(address addr, uint256 salt);
      function deploy(bytes memory code, uint256 salt) public returns(address){
          address addr;
          assembly {
              addr := create2(0, add(code, 0x20), mload(code), salt)
              if iszero(extcodesize(addr)) {
                  revert(0, 0)
              }
          }
          emit Deployed(addr, salt);
          return addr;
      }
```
