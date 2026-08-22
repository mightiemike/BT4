### Title
Native TRX sent to TVM precompiled-contract addresses via `CALL` (or direct transfer) is permanently and irrecoverably locked - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
`Program.callToPrecompiledAddress()` unconditionally transfers any TRX `value`/`endowment` attached to a `CALL`/`CALLCODE` targeting a precompiled-contract address (e.g. `0x...01` ecrecover, `0x...05` modexp, or TVM-native addresses such as `rewardBalanceAddr`/`voteCountAddr`) into that address's on-chain balance before invoking the precompile logic. Precompiled-contract addresses have no corresponding private key and no contract code capable of forwarding or refunding the balance, so any TRX sent to them is permanently locked, mirroring the "deposited assets cannot be withdrawn" bug class described in the report (Entropy/Executor accepting native asset deposits with no withdrawal path).

### Finding Description
When the TVM executes a `CALL` (or `CALLCODE`) whose target address resolves to a precompiled contract via `PrecompiledContracts.getContractForAddress()`, execution is routed to `Program.callToPrecompiledAddress()` instead of `callToAddress()`: [1](#0-0) 

Inside `callToPrecompiledAddress`, if `endowment > 0`, the code performs an unconditional TRX transfer from the caller to the precompile's `contextAddress` *before* the precompile is even executed, and this transfer is not reversed regardless of whether the precompile call subsequently succeeds or fails: [2](#0-1) 

The precompiled contract addresses themselves (`ecRecoverAddr`, `sha256Addr`, `modExpAddr`, `rewardBalanceAddr`, `voteCountAddr`, etc.) are fixed, low-value 21-byte addresses with no known private key: [3](#0-2) 

`PrecompiledContract.execute()` implementations only process calldata and return a result — none of them account for, forward, or expose any mechanism to withdraw TRX credited to their address. Because these addresses are not "Contract" accounts with corresponding bytecode/actuators (they're not real deployed contracts at all, just protocol-reserved addresses), there is no `WithdrawBalanceActuator`, TVM `suicide`, or any other actuator path that can move balance back out of them; the balance sits on an address nobody controls. This directly parallels the reported issue: value can be deposited into an address/contract, but the code that "owns" that address has no facility to release it.

### Impact Explanation
Any TRX sent to a precompiled-contract address — whether via a smart-contract `CALL(...).value(x)(...)` to e.g. `0x1`/`0x5`/`0x1000005`, or even via a plain `TransferContract` broadcast transaction to that same address (since it is a syntactically valid TRON address) — becomes permanently and irrecoverably locked. This is a direct, protocol-level asset-loss condition reachable by any anonymous account issuing a broadcast transaction or triggering a smart contract, with no privileged actor or key compromise required.

### Likelihood Explanation
High likelihood of accidental loss (e.g., a contract author mistakenly forwarding `msg.value` in a low-level call to what they believe is a normal address but happens to collide with a reserved precompile address, or a user mistakenly sending TRX directly to one of these well-known short addresses). It is also trivially reproducible by any user or contract intentionally, requiring only a standard `CALL` with nonzero value or a `TransferContract` to a precompile address — no special permissions, elevated access, or protocol bug beyond the missing accounting/withdrawal path.

### Recommendation
Short term: reject/refund (rather than silently transfer) any nonzero `value`/`endowment` on calls whose target resolves to a precompiled-contract address in `Program.callToPrecompiledAddress`, since these addresses cannot meaningfully receive or later release TRX. Alternatively, treat such addresses like TRON's Blackhole address by making the transfer explicit "burn" semantics that add to a resource pool rather than an inaccessible balance. Also consider validating and rejecting plain `TransferContract` transactions whose recipient address matches any reserved precompiled-contract address. Long term, add explicit test coverage asserting that value cannot be stranded at reserved/precompile addresses.

### Proof of Concept
1. Deploy a simple contract with a function that does `address(0x0000000000000000000000000000000000000001).call.value(x)("")` (or the equivalent low-level call targeting any precompile address such as `0x...05` for `modExp`, or a TVM-native address like `rewardBalanceAddr`).
2. Trigger the contract with `callValue = x` TRX.
3. Observe via `AccountStore.get(precompileAddress).getBalance()` that the balance at the precompile address increases by `x`, per the transfer at `Program.java:1706`.
4. Attempt to move that TRX out: there is no private key for the address, no `Contract`-typed account/code exists there, and no actuator (e.g. `WithdrawBalanceActuator`) provides a path to reclaim it — the TRX is permanently locked.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L1046-1056)
```java
    PrecompiledContracts.PrecompiledContract contract =
        PrecompiledContracts.getContractForAddress(codeAddress);
    if (contract != null) {
      if (program.isConstantCall()) {
        contract =  PrecompiledContracts.getOptimizedContractForConstant(contract);
      }
      program.callToPrecompiledAddress(msg, contract);
    } else {
      program.callToAddress(msg);
    }
    program.step();
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1701-1721)
```java
    // Charge for endowment - is not reversible by rollback
    if (!ArrayUtils.isEmpty(senderAddress) && !ArrayUtils.isEmpty(contextAddress)
        && senderAddress != contextAddress && msg.getEndowment().value().longValueExact() > 0) {
      if (!isTokenTransfer) {
        try {
          MUtil.transfer(deposit, senderAddress, contextAddress,
              msg.getEndowment().value().longValueExact());
        } catch (ContractValidateException e) {
          throw new BytecodeExecutionException("transfer failure");
        }
      } else {
        try {
          VMUtils
              .validateForSmartContract(deposit, senderAddress, contextAddress, tokenId, endowment);
        } catch (ContractValidateException e) {
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addTokenBalance(senderAddress, tokenId, -endowment);
        deposit.addTokenBalance(contextAddress, tokenId, endowment);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L133-172)
```java
  private static final DataWord ecRecoverAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000001");
  private static final DataWord sha256Addr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000002");
  private static final DataWord ripempd160Addr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000003");
  private static final DataWord identityAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000004");
  private static final DataWord modExpAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000005");
  private static final DataWord altBN128AddAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000006");
  private static final DataWord altBN128MulAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000007");
  private static final DataWord altBN128PairingAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000008");
  private static final DataWord batchValidateSignAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000009");
  private static final DataWord validateMultiSignAddr = new DataWord(
      "000000000000000000000000000000000000000000000000000000000000000a");
  private static final DataWord verifyMintProofAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000001");
  private static final DataWord verifyTransferProofAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000002");
  private static final DataWord verifyBurnProofAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000003");
  private static final DataWord merkleHashAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000004");
  private static final DataWord rewardBalanceAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000005");
  private static final DataWord isSrCandidateAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000006");
  private static final DataWord voteCountAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000007");
  private static final DataWord usedVoteCountAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000008");
  private static final DataWord receivedVoteCountAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000001000009");
  private static final DataWord totalVoteCountAddr = new DataWord(
      "000000000000000000000000000000000000000000000000000000000100000a");
```
