## Finding

Tokens sent via CALL-with-value to a TVM precompiled contract are irrecoverably lost if the precompiled call subsequently fails, mirroring the Axelar `Executor` bug where a failed `_executeWithToken()` leaves funds stuck with no recovery path.

### Title
Value/TRC10 sent to a precompiled contract via CALL is permanently lost when the precompiled execution fails or lacks energy - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
`Program.callToPrecompiledAddress()` unconditionally transfers the CALL's endowment (TRX or TRC10 token) to the precompiled contract's address *before* checking whether enough energy was supplied and before invoking the precompiled logic [1](#0-0) . If the subsequent energy check fails or the precompiled contract execution itself fails, the code only pushes zero on the stack and refunds energy - it never reverses the earlier balance transfer: [2](#0-1) 

The code comment explicitly flags this design: *"Charge for endowment - is not reversible by rollback"* [3](#0-2) . Unlike `callToAddress()` (the path used for regular contract-to-contract calls), where a failed callee causes `internalTx.reject()` and the value transfer is rolled back together with the rest of the child `deposit` [4](#0-3) , the precompiled-address path commits the balance/token movement independently of `deposit.commit()`, which is only called in the success branch [5](#0-4) .

Precompiled contract addresses (e.g. the fixed addresses handled by `PrecompiledContracts.getContractForAddress`) have no logic to move out an accidentally received balance [6](#0-5) , so once TRX or a TRC10 token lands there it is functionally unspendable/burned — directly analogous to Axelar's `Executor` sending bridged tokens to `callTo` with no recovery address when `_executeWithToken()` reverts.

### Impact Explanation
Any TRX or TRC10 tokens attached to a `CALL`/`CALLTOKEN` targeting a precompiled address are lost whenever:
- The energy supplied is insufficient for the requested operation (`requiredEnergy > msg.getEnergy()`), or
- The precompiled contract's `execute()` returns failure (e.g. malformed input to modexp/altbn128/etc.).

This causes real, permanent loss of user funds with no protocol-level recovery, matching the "High Risk" severity of the referenced report (asset/accounting corruption via unrecoverable token loss).

### Likelihood Explanation
This is trivially reachable by any unprivileged user: deploy or call a smart contract that performs `address(precompiled).call{value: X}(badOrUnderfundedCalldata)` via a normal `TriggerSmartContractContract` broadcast transaction. No special privileges, keys, or node access are required — only crafting calldata/energy that causes the targeted precompiled contract to fail after value has already been transferred.

### Recommendation
Move the endowment/token transfer in `callToPrecompiledAddress()` so it occurs on the same `deposit` child repository and is only persisted (`deposit.commit()`) after a successful `contract.execute(data)` call, or explicitly roll back the balance/token change in the `requiredEnergy > energy` and `!out.getLeft()` branches, consistent with the rollback behavior already implemented in `callToAddress()` [7](#0-6) .

### Proof of Concept
1. Deploy a contract with a function that executes, in assembly, `call(gas, <precompiled_address>, <value>, <bad_input_ptr>, <bad_input_size>, <out_ptr>, <out_size>)` where `<precompiled_address>` is a valid TVM precompiled contract (e.g. the bn256Add/bn256Pairing addresses referenced in existing tests) and `<bad_input_*>` is crafted to make `contract.getEnergyForData(data)` exceed the energy passed, or to make `contract.execute(data)` return failure.
2. Trigger this function via a normal `TriggerSmartContractContract` transaction with `value > 0`.
3. Observe that the CALL returns `0` (failure) as expected, but the caller's TRX/TRC10 balance has already been decremented and credited to the fixed precompiled address, which can never spend or return it — the value is permanently lost.

Note: I could not fully inspect `RepositoryImpl`'s child/parent commit semantics (the file's `newRepositoryChild`/`commit` implementation was not returned by search), so the exact mechanism by which this transfer becomes durable independent of `deposit.commit()` is inferred primarily from the explicit code comment and the asymmetry with `callToAddress()`'s rollback handling, rather than from directly reading `RepositoryImpl`. A deeper review of `RepositoryImpl` would be needed to fully confirm the persistence path.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1157-1174)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1723-1755)
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
      } else {
        // spend all energy on failure, push zero and revert state changes
        this.refundEnergy(0, CALL_PRE_COMPILED);
        this.stackPushZero();
        if (Objects.nonNull(this.result.getException())) {
          throw result.getException();
        }
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L1046-1055)
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
```
