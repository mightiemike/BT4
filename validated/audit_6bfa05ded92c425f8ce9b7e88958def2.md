Confirmed: the `call()` path unconditionally transfers `callValue`/`tokenValue` to `contractAddress` at [1](#0-0)  regardless of what the stored code does, and the actual code execution is entirely separate and gated only by `isNotEmpty(code)`.

### Title
Contracts deployed pre-ALLOW_TVM_CONSTANTINOPLE with malformed init code get 32 zero-byte runtime code that permanently locks all TRX/TRC10 sent to them - ([File: actuator/src/main/java/org/tron/core/vm/program/ProgramPrecompile.java])

### Summary
When `VMConfig.allowTvmConstantinople()` is `false`, `VMActuator.create()` derives the deployed contract's runtime code by statically scanning the raw constructor bytecode for a `RETURN`+`STOP` opcode pair via `ProgramPrecompile.getCode`, instead of executing the constructor and using its `RETURN` output. If no such pair exists in the submitted bytecode, `getCode` returns `new byte[DataWord.WORD_SIZE]` (32 zero bytes = 32 `STOP` opcodes), which is saved as the contract's permanent runtime code. Any subsequent CALL to that address executes only `STOP`, succeeds trivially, and can never move out TRX/TRC10 sent to it.

### Finding Description
In the pre-Constantinople legacy branch of contract creation, `VMActuator.create()` does not execute the constructor at all before persisting code; it statically parses `newSmartContract.getBytecode()`: [2](#0-1) 

`ProgramPrecompile.getCode` scans for the `RETURN` opcode immediately followed by `STOP`; if the pattern is never found (e.g., an attacker submits deployment bytecode consisting only of `PUSH`/`STOP`/junk opcodes with no `RETURN 0xf3` immediately followed by `STOP 0x00`), it falls back to returning 32 zero bytes when Constantinople is not active: [3](#0-2) 

These 32 zero bytes are then persisted via `rootRepository.saveCode(contractAddress, ...)`, becoming the contract's permanent runtime code. There is no validation rejecting empty/degenerate init code in this legacy path, and no exception is thrown as would happen in a normal EVM/TVM (Constantinople) flow where `Program.createContractImpl`/`VMActuator.execute` derive code from actual `HReturn` (execution result), guarded by exceptions like insufficient energy or `0xEF` prefix checks: [4](#0-3) 

On any later CALL to this address, `VMActuator.call()` fetches the stored code, finds it non-empty (32 zero bytes), and unconditionally transfers `callValue`/`tokenValue` to the contract address before/regardless of program execution, then executes a `Program` whose entire bytecode is repeated `STOP` (opcode `0x00`), which halts immediately with no exception and no revert: [5](#0-4) 

Because the stored bytecode contains zero non-`STOP` opcodes, it can never contain a `CALL`/`SELFDESTRUCT`/etc. capable of invoking `MUtil.transfer`/`MUtil.transferToken` to move funds back out; the account is a normal TRON smart-contract account (no private key), so funds sent to it become permanently unreachable.

### Impact Explanation
Any TRX or TRC10 tokens sent to such a contract (at deployment time via `callValue`/`tokenValue`, or in any later CALL) are permanently and irrecoverably locked, since the only code path ever stored for that address is `STOP` repeated, and there is no owner key or code path to invoke a transfer out. This is a direct, attacker-triggerable value-loss condition for any counterparty who is deceived into sending funds to such a contract (e.g., believing it is a legitimate contract from its ABI/name).

### Likelihood Explanation
This is only reachable while the network's `ALLOW_TVM_CONSTANTINOPLE` committee proposal (#26) has not yet been approved (`VMConfig.allowTvmConstantinople()` returns `false`), which is the default state (`allowTvmConstantinople = 0`) per `reference.conf` and `CommitteeConfig`, and this proposal is a one-way switch — `ProposalUtil.validator` rejects any value other than `1`, so once enabled it can never be turned back off: [6](#0-5) 
Consequently, on TRON mainnet (where Constantinople was activated years ago) this path is currently dead/unreachable, but it remains fully reachable and exploitable by any unprivileged user on any freshly bootstrapped private chain, devnet, or testnet that has not yet passed this proposal — a normal, unprivileged, no-special-permission condition for such networks. No admin action is required by the attacker; only the passive absence of a not-yet-approved governance flag, which is the out-of-the-box default.

### Recommendation
In the `!VMConfig.allowTvmConstantinople()` branch of `VMActuator.create()`, reject contract deployment (throw `ContractValidateException`/set an exception result) when `ProgramPrecompile.getCode` cannot find a valid `RETURN`+`STOP` delimiter, instead of silently substituting 32 zero bytes as runtime code. Alternatively, treat the fallback zero-byte result as "no code returned" and fail contract creation, refunding/reverting rather than persisting degenerate runtime code that can trap value.

### Proof of Concept
```java
// Pseudocode Java test (framework/src/test/java/org/tron/common/runtime/vm/...)
// Preconditions: committee.allowTvmConstantinople = 0 (default reference.conf)

@Test
public void testMalformedInitCodeLocksFunds() throws Exception {
  // init code with NO RETURN(0xf3)+STOP(0x00) pair, e.g. just PUSH1 0x00 STOP
  byte[] initCode = Hex.decode("600000"); // PUSH1 0x00, STOP

  Transaction deployTx = TvmTestUtils.generateDeploySmartContractAndGetTransaction(
      "Malformed", ownerAddr, "[]", Hex.toHexString(initCode), 100_000_000L /* callValue */,
      1_000_000_000L, 0, null);
  byte[] contractAddress = WalletUtil.generateContractAddress(deployTx);
  TvmTestUtils.processTransactionAndReturnRuntime(deployTx, rootRepository, null);

  byte[] storedCode = rootRepository.getCode(contractAddress);
  // Assert stored code is exactly 32 zero bytes (all STOP)
  Assert.assertArrayEquals(new byte[32], storedCode);

  // Send more TRX to the contract via a CALL
  long balanceBefore = rootRepository.getAccount(contractAddress).getBalance();
  Transaction callTx = TvmTestUtils.generateTriggerSmartContractAndGetTransaction(
      ownerAddr, contractAddress, new byte[0], 50_000_000L /* callValue */, 0);
  TVMTestResult result = TvmTestUtils.processTransactionAndReturnRuntime(callTx, rootRepository, null);

  Assert.assertNull(result.getRuntimeError()); // succeeds silently
  long balanceAfter = rootRepository.getAccount(contractAddress).getBalance();
  Assert.assertEquals(balanceBefore + 50_000_000L, balanceAfter);

  // Prove there is no possible code path to move funds out:
  // stored code contains only 0x00 (STOP) bytes -> no CALL/SELFDESTRUCT opcode present
  for (byte b : storedCode) {
    Assert.assertEquals(0x00, b & 0xff);
  }
  // Therefore funds are permanently stranded at contractAddress.
}
```

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L202-222)
```java
        if (TrxType.TRX_CONTRACT_CREATION_TYPE == trxType && !result.isRevert()) {
          byte[] code = program.getResult().getHReturn();
          if (code.length != 0 && VMConfig.allowTvmLondon() && code[0] == (byte) 0xEF) {
            if (null == result.getException()) {
              result.setException(Program.Exception.invalidCodeException());
            }
          }
          long saveCodeEnergy = (long) getLength(code) * EnergyCost.getCreateData();
          long afterSpend = program.getEnergyLimitLeft().longValue() - saveCodeEnergy;
          if (afterSpend < 0) {
            if (null == result.getException()) {
              result.setException(Program.Exception
                  .notEnoughSpendEnergy("save just created contract code",
                      saveCodeEnergy, program.getEnergyLimitLeft().longValue()));
            }
          } else {
            result.spendEnergy(saveCodeEnergy);
            if (VMConfig.allowTvmConstantinople()) {
              rootRepository.saveCode(program.getContractAddress().getNoLeadZeroesData(), code);
            }
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L443-446)
```java
    byte[] code = newSmartContract.getBytecode().toByteArray();
    if (!VMConfig.allowTvmConstantinople()) {
      rootRepository.saveCode(contractAddress, ProgramPrecompile.getCode(code));
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L507-560)
```java
    byte[] code = rootRepository.getCode(contractAddress);
    if (isNotEmpty(code)) {
      long feeLimit = trx.getRawData().getFeeLimit();
      if (feeLimit < 0 || feeLimit > rootRepository.getDynamicPropertiesStore().getMaxFeeLimit()) {
        logger.info("invalid feeLimit {}", feeLimit);
        throw new ContractValidateException("feeLimit must be >= 0 and <= "
            + rootRepository.getDynamicPropertiesStore().getMaxFeeLimit());
      }
      AccountCapsule caller = rootRepository.getAccount(callerAddress);
      long energyLimit;
      if (isConstantCall) {
        energyLimit = maxEnergyLimit;
      } else {
        AccountCapsule creator = rootRepository
            .getAccount(deployedContract.getInstance().getOriginAddress().toByteArray());
        energyLimit = getTotalEnergyLimit(creator, caller, contract, feeLimit, callValue);
      }

      long thisTxCPULimitInUs = calculateCpuLimitInUs(isConstantCall,
          rootRepository.getDynamicPropertiesStore().getMaxCpuTimeOfOneTx(),
          getCpuLimitInUsRatio(), CommonParameter.getInstance().getConstantCallTimeoutMs());
      long vmStartInUs = System.nanoTime() / VMConstant.ONE_THOUSAND;
      long vmShouldEndInUs = vmStartInUs + thisTxCPULimitInUs;
      ProgramInvoke programInvoke = ProgramInvokeFactory
          .createProgramInvoke(TrxType.TRX_CONTRACT_CALL_TYPE, executorType, trx,
              tokenValue, tokenId, blockCap.getInstance(), rootRepository, vmStartInUs,
              vmShouldEndInUs, energyLimit);
      if (isConstantCall) {
        programInvoke.setConstantCall();
      }
      rootInternalTx = new InternalTransaction(trx, trxType);
      this.program = new Program(code, contractAddress, programInvoke, rootInternalTx);
      if (VMConfig.allowTvmCompatibleEvm()) {
        this.program.setContractVersion(deployedContract.getContractVersion());
      }
      byte[] txId = TransactionUtil.getTransactionId(trx).getBytes();
      this.program.setRootTransactionId(txId);

      if (enableEventListener && isCheckTransaction()) {
        logInfoTriggerParser = new LogInfoTriggerParser(blockCap.getNum(), blockCap.getTimeStamp(),
            txId, callerAddress);
      }
    }

    program.getResult().setContractAddress(contractAddress);
    //transfer from callerAddress to targetAddress according to callValue

    if (callValue > 0) {
      MUtil.transfer(rootRepository, callerAddress, contractAddress, callValue);
    }
    if (VMConfig.allowTvmTransferTrc10() && tokenValue > 0) {
      MUtil.transferToken(rootRepository, callerAddress, contractAddress, String.valueOf(tokenId),
          tokenValue);
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/ProgramPrecompile.java (L31-54)
```java
  public static byte[] getCode(byte[] ops) {
    for (int i = 0; i < ops.length; ++i) {

      int op = ops[i] & 0xff;

      if (op == Op.RETURN && i + 1 < ops.length && ((ops[i + 1]) & 0xff) == Op.STOP) {
        byte[] ret;
        i++;
        ret = new byte[ops.length - i - 1];

        System.arraycopy(ops, i + 1, ret, 0, ops.length - i - 1);
        return ret;
      }

      if (op >= Op.PUSH1 && op <= Op.PUSH32) {
        i += op - Op.PUSH1 + 1;
      }
    }
    if (VMConfig.allowTvmConstantinople()) {
      return new byte[0];
    } else {
      return new byte[DataWord.WORD_SIZE];
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L202-215)
```java
      case ALLOW_TVM_CONSTANTINOPLE: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_3_6)) {
          throw new ContractValidateException(BAD_PARAM_ID);
        }
        if (value != 1) {
          throw new ContractValidateException(
              PRE_VALUE_NOT_ONE_ERROR + "ALLOW_TVM_CONSTANTINOPLE" + VALUE_NOT_ONE_ERROR);
        }
        if (dynamicPropertiesStore.getAllowTvmTransferTrc10() == 0) {
          throw new ContractValidateException(
              "[ALLOW_TVM_TRANSFER_TRC10] proposal must be approved "
                  + "before [ALLOW_TVM_CONSTANTINOPLE] can be proposed");
        }
        break;
```
