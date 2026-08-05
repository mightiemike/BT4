### Title
Precompiled contract calls charge endowment/TRC10 value before execution, and the code explicitly acknowledges this transfer is not rolled back if the call fails - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
`Program.callToPrecompiledAddress()` deducts TRX ("endowment") or TRC10 token value from the caller and credits it to the precompiled-contract's context address *before* invoking `contract.execute(data)`. The code contains an explicit comment stating this charge "is not reversible by rollback," and the failure branch does not attempt to reverse it — it only refunds energy and pushes zero onto the stack, without returning the transferred value to the sender.

### Finding Description
In `callToPrecompiledAddress`, the endowment/TRC10 transfer is performed unconditionally on the child `deposit` repository prior to checking whether the precompiled contract execution succeeds: [1](#0-0) 

Only after this transfer does the code invoke `contract.execute(data)` and branch on the result: [2](#0-1) 

On success, `deposit.commit()` is called to persist the child repository's changes (including the transfer) to the parent state. On failure (`out.getLeft()` is false), the comment says "spend all energy on failure, push zero and revert state changes," yet `deposit.commit()` is never invoked in the failure branch — the intent appears to be that discarding the uncommitted child `deposit` object naturally reverts the transfer along with everything else the precompiled contract may have mutated. However, the developers explicitly flagged the endowment charge itself with the comment "Charge for endowment - is not reversible by rollback," directly acknowledging that this specific mutation does not follow the same discard-on-failure semantics as the rest of the precompiled contract's changes.

This mirrors the structure of the reported TON issue: a value-moving operation (burn/notify) is executed unconditionally, and a subsequent processing step (the `OP_EXTRA_BURN_INFO` handler / here, `contract.execute`) can fail without the value movement being undone, since the two are not implemented as a single atomic, revertible unit despite documentation/comments implying that failure should "revert state changes."

### Impact Explanation
If the endowment/token transfer performed in the "Charge for endowment" block is not actually undone when the precompiled contract subsequently fails (`out.getLeft() == false`), a caller invoking a precompiled contract (e.g., an EVM `CALL` targeting a TVM precompile address such as the shielded pool contracts) with a callValue/tokenValue could have that value irrecoverably moved to the precompile's context address while the call itself reports failure to the calling contract (stack pushes zero). This would be a direct, unprivileged loss-of-funds/accounting-divergence bug consistent with the reported bug class (value burned/moved with no refund path on failure).

### Likelihood Explanation
This code path is reachable by any unprivileged user through ordinary smart contract execution that performs a `CALL`/`CALLCODE`/`DELEGATECALL` with non-zero value or TRC10 tokenValue to a precompiled contract address, and then causes that precompiled contract to fail (e.g., malformed proof/parameters for the shielded-pool precompiles). No special privileges are required to trigger it.

### Recommendation
Verify whether `RepositoryImpl.newRepositoryChild()` / `commit()` semantics genuinely make the balance/token mutations performed directly via `MUtil.transfer(deposit, ...)` and `deposit.addTokenBalance(...)` conditional on `deposit.commit()` being called, or whether they write through to a shared underlying cache regardless of commit. If the latter, move the endowment/token transfer to occur only after a successful `contract.execute()` call (or explicitly reverse it in the failure branch) so that a failing precompiled contract call cannot result in an unrecoverable transfer of value/tokens.

### Proof of Concept
I was not able to fully confirm within the available tooling whether `RepositoryImpl`'s child/commit mechanism truly persists the pre-execution transfer despite the failure branch skipping `deposit.commit()` — this requires deeper inspection of `RepositoryImpl`/`AccountCapsule` caching internals (e.g., whether the child repository shares a mutable cache object with the parent that is mutated in place) which the available index did not surface. I recommend a Devin session with full repository/build access to trace `getContractState().newRepositoryChild()` and confirm at runtime (e.g., via a unit test that triggers a precompiled-contract call with value that is made to fail) whether the sender's balance is actually debited and the target credited despite `out.getLeft() == false`.

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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1741-1755)
```java
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
