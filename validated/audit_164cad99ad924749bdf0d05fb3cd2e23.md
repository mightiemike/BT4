## Finding

### Title
Contract Storage Not Cleared on SELFDESTRUCT, Enabling Stale-State Inheritance on Address Reuse - (File: `chainbase/src/main/java/org/tron/core/db/TransactionTrace.java`)

### Summary
The reported bug class (`registry_service::remove_investor` deleting the main record but leaving stale attributes behind, which get inherited by a new record reusing the same ID) maps to java-tron's handling of contract self-destruction. When a contract executes `SUICIDE`/`SUICIDE2`, java-tron deletes the account, contract-metadata, code, and ABI entries for that address, but does not clear the contract's key/value storage rows (state variables). If a new contract is later deployed to the same address (e.g. via `CREATE2`, or the account-recreation path explicitly acknowledged in the codebase), it can inherit the previous contract's leftover storage.

### Finding Description
`Program.suicide()`/`suicide2()` in [1](#0-0)  transfer balance/TRC10/frozen resources to the inheritor and call `getResult().addDeleteAccount(this.getContractAddress())`, but never touch the contract's storage trie.

The actual deletion, performed at transaction finalization, only removes account/contract/code/abi records: [2](#0-1) 

The same limited scope is mirrored in the repository layer used during VM execution, which likewise deletes only code, account, and contract store entries — never storage rows: [3](#0-2) 

Notably, the codebase's own comment acknowledges that address reuse after `SUICIDE` is a real, anticipated scenario for other kinds of associated data (delegated resources), and special-cases it there: [4](#0-3) 

That comment shows the developers are aware that "contract suicide, re-create" is a real address-reuse path, but the fix was only applied to the delegated-resource accounting side — not to the underlying contract *storage* (state variables), which is the direct analog of "attributes" in the external report. No corresponding storage-clearing call (e.g. clearing `StorageRowStore`/`Storage` entries keyed by the contract address) exists in the suicide path or in `deleteContract`.

### Impact Explanation
If a contract self-destructs and a new contract is subsequently created at the same address (via `CREATE2` with attacker-controlled salt/init-code, which is explicitly supported and tested in this codebase per `Create2Test.java`), the new contract's storage slots may read back stale values left by the destroyed contract instead of starting from zero. Depending on the new contract's logic (e.g. treating a non-zero storage slot as "already initialized," "owner already set," or "balance already credited"), this can lead to unauthorized privilege/ownership assumption, double-accounting, or logic bypass in the new contract deployed on-chain — a direct on-chain state/accounting corruption analogous to the reported "new investor inherits old attributes" bug.

### Likelihood Explanation
Exploitation only requires an attacker to deploy their own contract with a `SELFDESTRUCT` path and later redeploy via `CREATE2` to the same address — both are ordinary, permissionless operations reachable by any account submitting a `TriggerSmartContract`/`CreateSmartContract` transaction; no privileged role or leaked key is required.

### Recommendation
When processing `addDeleteAccount`/`deleteContract`, also clear all storage rows associated with the destroyed contract address (and consider whether other keyed side-tables, e.g. delegated-resource or vote records for the address, still require cleanup beyond what `UnDelegateResourceProcessor` already special-cases) before the address can be reused by a subsequent `CREATE`/`CREATE2`.

### Proof of Concept
1. Deploy Contract A at address `X`.
2. Contract A writes to storage slot `S` (e.g., sets an "owner" or "initialized" flag).
3. Contract A calls `SUICIDE`/`SUICIDE2`; java-tron removes A's account/code/contract records via `TransactionTrace.deleteContract` [2](#0-1)  but leaves storage slot `S` untouched.
4. Deploy Contract B via `CREATE2` using a salt/init-code chosen so the resulting address is again `X`.
5. Contract B reads storage slot `S` at construction/first call and observes the stale non-zero value left by Contract A, allowing it to bypass an intended zero-initialization check (e.g., skip owner-assignment logic or treat itself as already funded/authorized).

**Note on confidence:** I was not able to fully trace whether any other code path (e.g., a `Storage` cache eviction routine or a separate deprecation/cleanup job) clears storage rows for a destroyed address elsewhere in the codebase; the `TvmIssueVerifierTest.java` and `Create2Test.java` test files reference `isNewContract`/CREATE2 scenarios extensively, which may indicate this exact class of issue has already been considered or mitigated elsewhere in ways I could not fully confirm from the available index. This should be verified against those test files and the full `Storage`/`StorageRowStore` clearing logic before treating this as conclusively unpatched.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L451-516)
```java
  public void suicide(DataWord obtainerAddress) {

    byte[] owner = getContextAddress();
    byte[] obtainer = obtainerAddress.toTronAddress();

    if (VMConfig.allowTvmVote()) {
      withdrawRewardAndCancelVote(owner, getContractState());
    }

    long balance = getContractState().getBalance(owner);

    if (logger.isDebugEnabled()) {
      logger.debug("Transfer to: [{}] heritage: [{}]",
          Hex.toHexString(obtainer),
          balance);
    }

    increaseNonce();

    InternalTransaction internalTx = addInternalTx(null, owner, obtainer, balance, null,
        "suicide", nonce, getContractState().getAccount(owner).getAssetMapV2());

    int ADDRESS_SIZE = VMUtils.getAddressSize();
    if (FastByteComparisons.compareTo(owner, 0, ADDRESS_SIZE, obtainer, 0, ADDRESS_SIZE) == 0) {
      // if owner == obtainer just zeroing account according to Yellow Paper
      getContractState().addBalance(owner, -balance);
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (VMConfig.allowTvmTransferTrc10()) {
        getContractState().addBalance(blackHoleAddress, balance);
        MUtil.transferAllToken(getContractState(), owner, blackHoleAddress);
      }
    } else {
      createAccountIfNotExist(getContractState(), obtainer);
      try {
        MUtil.transfer(getContractState(), owner, obtainer, balance);
        if (VMConfig.allowTvmTransferTrc10()) {
          MUtil.transferAllToken(getContractState(), owner, obtainer);
        }
      } catch (ContractValidateException e) {
        if (VMConfig.allowTvmConstantinople()) {
          throw new TransferException(
              "transfer all token or transfer all trx failed in suicide: %s", e.getMessage());
        }
        throw new BytecodeExecutionException("transfer failure");
      }
    }
    if (VMConfig.allowTvmFreeze()) {
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (FastByteComparisons.isEqual(owner, obtainer)) {
        transferDelegatedResourceToInheritor(owner, blackHoleAddress, getContractState());
      } else {
        transferDelegatedResourceToInheritor(owner, obtainer, getContractState());
      }
    }
    if (VMConfig.allowTvmFreezeV2()) {
      byte[] Inheritor =
          FastByteComparisons.isEqual(owner, obtainer)
              ? getContractState().getBlackHoleAddress()
              : obtainer;
      long expireUnfrozenBalance = transferFrozenV2BalanceToInheritor(owner, Inheritor, getContractState());
      if (expireUnfrozenBalance > 0 && internalTx != null) {
        internalTx.setValue(internalTx.getValue() + expireUnfrozenBalance);
      }
    }
    getResult().addDeleteAccount(this.getContractAddress());
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L373-378)
```java
  public void deleteContract(byte[] address) {
    abiStore.delete(address);
    codeStore.delete(address);
    accountStore.delete(address);
    contractStore.delete(address);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L486-491)
```java
  @Override
  public void deleteContract(byte[] address) {
    getCodeStore().delete(address);
    getAccountStore().delete(address);
    getContractStore().delete(address);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L107-113)
```java
          /* For example, in a scenario where a regular account can be upgraded to a contract
          account through an interface, the account information will be cleared after the
          contract suicide, and this account will be converted to a regular account in the future */
          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
```
