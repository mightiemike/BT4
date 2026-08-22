### Title
TRX/TRC10 endowment sent to a precompiled contract address is permanently trapped when the precompile short-circuits without processing – (`File: actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
`Program.callToPrecompiledAddress()` transfers the call's `endowment` (TRX or TRC10 value attached to a `CALL`) to the precompiled contract's context address **before** invoking `contract.execute(data)`. Several precompiled contracts (e.g. `DelegatableResource`, `ResourceV2`, and other data-length/format validators in `PrecompiledContracts.java`) immediately return a default/zero result when the supplied `data` is malformed or of unexpected length, performing no state-changing logic at all. Because these precompiled addresses (e.g. `0x100000b`, `0x100000c`, …) are not real accounts and have no owner, no withdraw method, and no way to be reached again with a corrective transaction, any value sent alongside a malformed/short call is irrecoverably stuck at that address — mirroring the reported bug class where a payable function silently returns without refunding attached value.

### Finding Description
In `callToPrecompiledAddress`, the endowment transfer occurs unconditionally once `senderAddress != contextAddress && endowment > 0`, explicitly commented as "Charge for endowment - is not reversible by rollback": [1](#0-0) 

This happens strictly before `contract.execute(data)` is called: [2](#0-1) 

Multiple precompiled contracts validate `data.length` and, on mismatch, return a zeroed/default result without performing the intended resource/state operation — analogous to the `bridgeRingToAbstract()` empty-array early return that never processes payment logic: [3](#0-2) [4](#0-3) 

Precompiled contract addresses are registered purely as dispatch targets, not as normal accounts with balance-management logic: [5](#0-4) 

Since these addresses have no associated actuator/withdraw mechanism to retrieve a stray balance, once TRX/TRC10 lands there it is permanently unreachable — the same "trapped funds, no refund logic" root cause as the original finding.

### Impact Explanation
Any contract (or contract triggered by an anonymous `TriggerSmartContract` transaction) that performs a low-level `CALL`/`DELEGATECALL` with a non-zero `value`/token amount to one of the FreezeV2/vote/proposal precompile addresses, while supplying malformed or wrong-length calldata, will have that value irreversibly transferred to the precompile address before the precompile's early-return logic runs. There is no owner key, actuator, or opcode path to later move that balance out, resulting in a permanent, unrecoverable loss of user TRX/TRC10 — a direct funds-loss condition reachable from ordinary, unprivileged contract execution.

### Likelihood Explanation
This is reachable purely through normal TVM execution triggered by any user's broadcast transaction (`TriggerSmartContract`) invoking a contract that performs a `CALL` with value to a known precompiled address (which are fixed, low, and publicly known addresses like `0xa`, `0x1000001`, `0x100000b`, etc.). No privileged role, leaked key, or malicious peer is required — an ordinary user error (or a poorly written/malicious wrapper contract that forwards value with unchecked calldata) suffices to trigger the fund-trapping path. This mirrors the "accidental send" scenario in the original report exactly.

### Recommendation
In `Program.callToPrecompiledAddress`, do not transfer `endowment` to the precompiled address before the precompile confirms it will/can act on the value, or reject calls (revert / push zero without committing the transfer) when `msg.getEndowment().value() > 0` targets a precompiled contract, since none of the current precompiles are designed to receive or account for TRX/TRC10 value. Alternatively, add value-refund logic in any precompile path that returns without processing, or explicitly disallow non-zero endowment for precompiled contract calls at the dispatch layer.

### Proof of Concept
1. Deploy a contract with a function that performs a raw `call{value: X}(precompileAddress, malformedData)` (e.g. targeting `resourceV2Addr` = `0x100000c` with `data.length != 3*WORD_SIZE`).
2. Trigger the function via `TriggerSmartContract` with `call_value = X`.
3. Observe: `Program.callToPrecompiledAddress` transfers `X` TRX to the precompile's context address (`deposit.addBalance`/`MUtil.transfer` executed prior to `contract.execute`), then `ResourceV2.execute()` returns `Pair.of(true, DataWord.ZERO().getData())` at line 2159 without moving/using the value.
4. The `X` TRX now resides at the precompile address with no owner private key and no protocol path to retrieve it — permanent loss, exactly analogous to the reported `bridgeRingToAbstract()` issue.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1723-1747)
```java
    long requiredEnergy = contract.getEnergyForData(data);
    if (requiredEnergy > msg.getEnergy().longValue()) {
      // Not need to throw an exception, method caller needn't know that
      // regard as consumed the energy
      this.refundEnergy(0, CALL_PRE_COMPILED); //matches cpp logic
      this.stackPushZero();
    } else {
      // Delegate or not. if is delegated, we will use msg sender, otherwise use contract address
      if (msg.getOpCode() == Op.DELEGATECALL) {
        contract.setCallerAddress(getCallerAddress().toTronAddress());
      } else {
        contract.setCallerAddress(getContextAddress());
      }
      // this is the depositImpl, not contractState as above
      contract.setRepository(deposit);
      contract.setResult(this.result);
      contract.setConstantCall(isConstantCall());
      contract.setVmShouldEndInUs(getVmShouldEndInUs());
      Pair<Boolean, byte[]> out = contract.execute(data);

      if (out.getLeft()) { // success
        this.refundEnergy(msg.getEnergy().longValue() - requiredEnergy, CALL_PRE_COMPILED);
        this.stackPushOne();
        returnDataBuffer = out.getRight();
        deposit.commit();
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L120-176)
```java
  // FreezeV2 PrecompileContracts
  private static final GetChainParameter getChainParameter = new GetChainParameter();
  private static final AvailableUnfreezeV2Size availableUnfreezeV2Size = new AvailableUnfreezeV2Size();
  private static final UnfreezableBalanceV2 unfreezableBalanceV2 = new UnfreezableBalanceV2();
  private static final ExpireUnfreezeBalanceV2 expireUnfreezeBalanceV2 = new ExpireUnfreezeBalanceV2();
  private static final DelegatableResource delegatableResource = new DelegatableResource();
  private static final ResourceV2 resourceV2 = new ResourceV2();
  private static final CheckUnDelegateResource checkUnDelegateResource = new CheckUnDelegateResource();
  private static final ResourceUsage resourceUsage = new ResourceUsage();
  private static final TotalResource totalResource = new TotalResource();
  private static final TotalDelegatedResource totalDelegatedResource = new TotalDelegatedResource();
  private static final TotalAcquiredResource totalAcquiredResource = new TotalAcquiredResource();

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

  // FreezeV2 PrecompileContracts
  private static final DataWord getChainParameterAddr = new DataWord(
      "000000000000000000000000000000000000000000000000000000000100000b");
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L2134-2146)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data == null || data.length != 2 * WORD_SIZE) {
        return Pair.of(true, DataWord.ZERO().getData());
      }

      DataWord[] words = DataWord.parseArray(data);
      byte[] address = words[0].toTronAddress();
      long type = words[1].longValueSafe();

      long result = FreezeV2Util.queryDelegatableResource(address, type, getDeposit());
      return Pair.of(true, longTo32Bytes(result));
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L2156-2174)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data == null || data.length != 3 * WORD_SIZE) {
        return Pair.of(true, DataWord.ZERO().getData());
      }

      DataWord[] words = DataWord.parseArray(data);
      byte[] target = words[0].toTronAddress();
      byte[] from = words[1].toTronAddress();
      long type = words[2].longValueSafe();

      long balance;
      if (Arrays.equals(from, target)) {
        balance = FreezeV2Util.queryUnfreezableBalanceV2(from, type, getDeposit());
      } else {
        balance = FreezeV2Util.queryResourceV2(from, target, type, getDeposit());
      }
      return Pair.of(true, longTo32Bytes(balance));
    }
```
