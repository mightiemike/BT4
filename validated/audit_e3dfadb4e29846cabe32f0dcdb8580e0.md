Confirmed: `RepositoryImpl` (the root repository used by `VMActuator`) buffers all writes in in-memory caches (`accountCache`, etc.) and only persists them to the underlying stores when `commit()` is explicitly called [1](#0-0) . This confirms that changes written directly on `rootRepository` before `commit()` is invoked are not yet durable, but they ARE immediately visible to subsequent reads from that same `rootRepository` instance (read-your-writes within the same execution context), since `addBalance`/`getAccount` operate on the shared `accountCache` [2](#0-1) .

### Title
Endowment transferred to callee's balance on the un-committed root repository before the callee's bytecode executes, exposing pre-effects state to reentrant callback logic - (File: actuator/src/main/java/org/tron/core/actuator/VMActuator.java)

### Summary
`VMActuator.call()` performs the `callValue`/`tokenValue` transfer from the caller to the target contract directly on `rootRepository` via `MUtil.transfer(rootRepository, ...)` **before** the target contract's bytecode is executed by `VM.play(program, ...)` in `execute()` [3](#0-2) [4](#0-3) . This is structurally analogous to the Timeswap `lend()` bug: an accounting-relevant balance mutation is applied to shared state, and only afterward does the "callback" (execution of arbitrary, potentially attacker-controlled bytecode at `contractAddress`) run — with the final effects only being made durable via `rootRepository.commit()` at the very end of `execute()` [5](#0-4) .

### Finding Description
In `VMActuator.call()`, the credit of `callValue`/`tokenValue` to `contractAddress` happens straight on the single, shared `rootRepository` object rather than on an isolated child repository that would only be merged into the parent if the ensuing call succeeds [6](#0-5) . Compare this to the safer nested-call pattern implemented deeper in the interpreter, `Program.callToAddress()`, which creates a **child** deposit (`getContractState().newRepositoryChild()`), performs the value transfer on that child, and only `deposit.commit()`s it into the parent after the sub-call succeeds [7](#0-6) [8](#0-7) .

At the outermost (top-level transaction) call, however, `VMActuator.call()` mutates `rootRepository` directly and only afterward runs the contract bytecode (`VM.play`), meaning any code reachable from the entry point (including code invoked transitively) observes its own already-credited balance and can act on state that has not yet passed through the exception/revert-handling path in `execute()`. Because `RepositoryImpl.commit()` is a no-op for durability purposes until called, and the exception path in `execute()` (`result.getException() != null || result.isRevert()`) does not call `rootRepository.commit()` [9](#0-8) , the on-disk state is technically protected against a *reverted* top-level call. But the risk mirrors the reported bug class in a subtler way: the balance-changing side effect (`MUtil.transfer`) is committed to the in-memory root cache *before* the "callback" (bytecode at the destination address, which is fully attacker/user-controlled) executes, so any accounting logic performed by that callee (e.g., querying its own balance mid-call for further business logic, triggering re-entrant `CALL`s back into other contracts) sees a state where the transfer effect has already landed but the transaction's overall success/failure has not yet been determined.

### Impact Explanation
If a called contract reenters (e.g., calls back into the caller's contract, another exchange/precompiled TVM function, or a different user contract) before the top-level transaction concludes, it can observe and act upon a balance state that reflects the value transfer but not the full set of invariants the top-level actuator was supposed to enforce atomically. This is the same "effects performed before all interactions/validation complete" pattern flagged in the Timeswap report, applied to the entry point of TVM contract execution rather than to a specific DeFi protocol function.

### Likelihood Explanation
Reachable by any unprivileged user submitting a `TriggerSmartContract` transaction with a nonzero `callValue` to a contract they control or that has arbitrary receive/fallback logic — no privileged role is required. The `MessageCall`/`callToAddress` path deeper in the interpreter correctly isolates each nested call via child repositories, which somewhat limits practical exploitability of the top-level pattern to interactions that specifically depend on top-level `rootRepository` state ordering; a full impact assessment requires deeper tracing of every place that reads `rootRepository` balances/state mid-execution, which I was not able to exhaustively enumerate within the available search budget.

### Recommendation
Route the top-level `callValue`/`tokenValue` transfer in `VMActuator.call()`/`create()` through the same child-repository pattern used in `Program.callToAddress()`/`createContractImpl()` — i.e., perform the endowment transfer on a repository that is only merged into `rootRepository` after `VM.play()` completes successfully — so that all effects of a top-level transaction, including the initial value transfer, are only durably observable once the entire transaction (including all nested/reentrant calls) has concluded without exception or revert.

### Proof of Concept
Not independently reproduced; based on static code-path analysis of `VMActuator.call()` [10](#0-9)  versus the child-repository isolation used in `Program.callToAddress()` [11](#0-10) . A concrete PoC would require deploying two cooperating contracts (caller and reentrant callee) and demonstrating a state inconsistency exploitable via a value-carrying `TriggerSmartContract` call; this was not executed as part of this analysis.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L736-758)
```java
  @Override
  public long addBalance(byte[] address, long value) {
    AccountCapsule accountCapsule = getAccount(address);
    if (accountCapsule == null) {
      accountCapsule = createAccount(address, Protocol.AccountType.Normal);
    }

    long balance = accountCapsule.getBalance();
    if (value == 0) {
      return balance;
    }

    if (value < 0 && balance < -value) {
      throw new RuntimeException(
          StringUtil.createReadableString(accountCapsule.createDbKey())
              + " insufficient balance");
    }
    accountCapsule.setBalance(addExact(balance, value, VMConfig.disableJavaLangMath()));
    Key key = Key.create(address);
    accountCache.put(key, Value.create(accountCapsule,
         accountCache.get(key).getType().addType(Type.DIRTY)));
    return accountCapsule.getBalance();
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L765-783)
```java
  @Override
  public void commit() {
    Repository repository = null;
    if (parent != null) {
      repository = parent;
    }
    commitAccountCache(repository);
    commitCodeCache(repository);
    commitContractCache(repository);
    commitContractStateCache(repository);
    commitStorageCache(repository);
    commitDynamicCache(repository);
    commitDelegatedResourceCache(repository);
    commitVotesCache(repository);
    commitDelegationCache(repository);
    commitDelegatedResourceAccountIndexCache(repository);
    commitTransientStorage(repository);
    commitNewContractCache(repository);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L192-193)
```java
        VM.play(program, OperationRegistry.getTable());
        result = program.getResult();
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L234-248)
```java
        if (result.getException() != null || result.isRevert()) {
          result.getDeleteAccounts().clear();
          result.getLogInfoList().clear();
          //result.resetFutureRefund();
          result.rejectInternalTransactions();

          if (result.getException() != null) {
            if (!(result.getException() instanceof TransferException)) {
              program.spendAllEnergy();
            }
            result.setRuntimeError(result.getException().getMessage());
            throw result.getException();
          } else {
            result.setRuntimeError("REVERT opcode executed");
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L249-250)
```java
        } else {
          rootRepository.commit();
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L462-562)
```java
  private void call()
      throws ContractValidateException {

    if (!rootRepository.getDynamicPropertiesStore().supportVM()) {
      logger.info("vm work is off, need to be opened by the committee");
      throw new ContractValidateException("VM work is off, need to be opened by the committee");
    }

    TriggerSmartContract contract = ContractCapsule.getTriggerContractFromTransaction(trx);
    if (contract == null) {
      return;
    }

    if (contract.getContractAddress() == null) {
      throw new ContractValidateException("Cannot get contract address from TriggerContract");
    }

    byte[] contractAddress = contract.getContractAddress().toByteArray();

    ContractCapsule deployedContract = rootRepository.getContract(contractAddress);
    if (null == deployedContract) {
      logger.info("No contract or not a smart contract");
      throw new ContractValidateException("No contract or not a smart contract");
    }

    long callValue = contract.getCallValue();
    long tokenValue = 0;
    long tokenId = 0;
    if (VMConfig.allowTvmTransferTrc10()) {
      tokenValue = contract.getCallTokenValue();
      tokenId = contract.getTokenId();
    }

    if (StorageUtils.getEnergyLimitHardFork()) {
      if (callValue < 0) {
        throw new ContractValidateException("callValue must be >= 0");
      }
      if (tokenValue < 0) {
        throw new ContractValidateException("tokenValue must be >= 0");
      }
    }

    byte[] callerAddress = contract.getOwnerAddress().toByteArray();
    checkTokenValueAndId(tokenValue, tokenId);

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

  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1000-1174)
```java
  public void callToAddress(MessageCall msg) {
    returnDataBuffer = null; // reset return buffer right before the call

    if (getCallDeep() == MAX_DEPTH) {
      stackPushZero();
      refundEnergy(msg.getEnergy().longValue(), " call deep limit reach");
      return;
    }

    byte[] data = memoryChunk(msg.getInDataOffs().intValue(), msg.getInDataSize().intValue());

    // FETCH THE SAVED STORAGE
    byte[] codeAddress = msg.getCodeAddress().toTronAddress();
    byte[] senderAddress = getContextAddress();

    byte[] contextAddress;
    if (msg.getOpCode() == Op.CALLCODE || msg.getOpCode() == Op.DELEGATECALL) {
      contextAddress = senderAddress;
    } else {
      contextAddress = codeAddress;
    }

    if (logger.isDebugEnabled()) {
      logger.debug(Op.getNameOf(msg.getOpCode())
              + " for existing contract: address: [{}], outDataOffs: [{}], outDataSize: [{}]  ",
          Hex.toHexString(contextAddress), msg.getOutDataOffs().longValue(),
          msg.getOutDataSize().longValue());
    }

    Repository deposit = getContractState().newRepositoryChild();

    // 2.1 PERFORM THE VALUE (endowment) PART
    long endowment;
    try {
      endowment = msg.getEndowment().value().longValueExact();
    } catch (ArithmeticException e) {
      if (VMConfig.allowTvmConstantinople()) {
        refundEnergy(msg.getEnergy().longValue(), "endowment out of long range");
        throw new TransferException("endowment out of long range");
      } else {
        throw e;
      }
    }
    // transfer TRX validation
    byte[] tokenId = null;

    checkTokenId(msg);

    boolean isTokenTransfer = isTokenTransfer(msg);

    if (!isTokenTransfer) {
      long senderBalance = deposit.getBalance(senderAddress);
      if (senderBalance < endowment) {
        stackPushZero();
        refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
        return;
      }
    } else {
      // transfer trc10 token validation
      tokenId = String.valueOf(msg.getTokenId().longValue()).getBytes();
      long senderBalance = deposit.getTokenBalance(senderAddress, tokenId);
      if (senderBalance < endowment) {
        stackPushZero();
        refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
        return;
      }
    }

    // FETCH THE CODE
    AccountCapsule accountCapsule = getContractState().getAccount(codeAddress);

    byte[] programCode =
        accountCapsule != null ? getContractState().getCode(codeAddress) : EMPTY_BYTE_ARRAY;

    // only for TRX, not for token
    long contextBalance = 0L;
    if (byTestingSuite()) {
      // This keeps track of the calls created for a test
      getResult().addCallCreate(data, contextAddress,
          msg.getEnergy().getNoLeadZeroesData(),
          msg.getEndowment().getNoLeadZeroesData());
    } else if (!ArrayUtils.isEmpty(senderAddress) && !ArrayUtils.isEmpty(contextAddress)
        && senderAddress != contextAddress && endowment > 0) {
      createAccountIfNotExist(deposit, contextAddress);
      if (!isTokenTransfer) {
        try {
          VMUtils
              .validateForSmartContract(deposit, senderAddress, contextAddress, endowment);
        } catch (ContractValidateException e) {
          if (VMConfig.allowTvmConstantinople()) {
            refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
            throw new TransferException("transfer trx failed: %s", e.getMessage());
          }
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addBalance(senderAddress, -endowment);
        contextBalance = deposit.addBalance(contextAddress, endowment);
      } else {
        try {
          VMUtils.validateForSmartContract(deposit, senderAddress, contextAddress,
              tokenId, endowment);
        } catch (ContractValidateException e) {
          if (VMConfig.allowTvmConstantinople()) {
            refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
            throw new TransferException("transfer trc10 failed: %s", e.getMessage());
          }
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addTokenBalance(senderAddress, tokenId, -endowment);
        deposit.addTokenBalance(contextAddress, tokenId, endowment);
      }
    }

    // CREATE CALL INTERNAL TRANSACTION
    increaseNonce();
    HashMap<String, Long> tokenInfo = new HashMap<>();
    if (isTokenTransfer) {
      tokenInfo.put(new String(stripLeadingZeroes(tokenId)), endowment);
    }
    InternalTransaction internalTx = addInternalTx(null, senderAddress, contextAddress,
        !isTokenTransfer ? endowment : 0, data, "call", nonce,
        !isTokenTransfer ? null : tokenInfo);
    ProgramResult callResult = null;
    if (isNotEmpty(programCode)) {
      long vmStartInUs = System.nanoTime() / 1000;
      DataWord callValue;
      if (msg.getOpCode() == Op.DELEGATECALL) {
        callValue = getCallValue();
      } else {
        callValue = msg.getEndowment();
      }
      ProgramInvoke programInvoke = ProgramInvokeFactory.createProgramInvoke(
          this, new DataWord(contextAddress),
          msg.getOpCode() == Op.DELEGATECALL ? getCallerAddress() : getContractAddress(),
          !isTokenTransfer ? callValue : DataWord.ZERO(),
          !isTokenTransfer ? DataWord.ZERO() : callValue,
          !isTokenTransfer ? DataWord.ZERO() : msg.getTokenId(),
          contextBalance, data, deposit,
          msg.getOpCode() == Op.STATICCALL || isStaticCall(),
          byTestingSuite(), vmStartInUs, getVmShouldEndInUs(), msg.getEnergy().longValueSafe());
      if (isConstantCall()) {
        programInvoke.setConstantCall();
      }
      Program program = new Program(programCode, codeAddress, programInvoke, internalTx);
      program.setRootTransactionId(this.rootTransactionId);
      if (VMConfig.allowTvmCompatibleEvm()) {
        program.setContractVersion(invoke.getDeposit()
            .getContract(codeAddress).getContractVersion());
      }
      VM.play(program, OperationRegistry.getTable());
      callResult = program.getResult();

      getTrace().merge(program.getTrace());
      getResult().merge(callResult);
      // always commit nonce
      this.nonce = program.nonce;

      if (callResult.getException() != null || callResult.isRevert()) {
        logger.debug("contract run halted by Exception: contract: [{}], exception: [{}]",
            Hex.toHexString(contextAddress),
            callResult.getException());
        internalTx.reject();

        callResult.rejectInternalTransactions();

        stackPushZero();

        if (callResult.getException() != null) {
          return;
        }
      } else {
        // 4. THE FLAG OF SUCCESS IS ONE PUSHED INTO THE STACK
        deposit.commit();
        stackPushOne();
      }
```
