### Title
Dual-Store Delegated Resource Accounting Desync via TVM `freezebalance` Native Contract - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java])

### Summary
`FreezeBalanceProcessor` (the TVM-callable native contract implementation of `FreezeBalanceContract`, reachable when a deployed smart contract executes the freeze opcode) updates `DelegatedResourceStore` and the delegating/receiving `AccountCapsule` balances, but never touches `DelegatedResourceAccountIndexStore`. This mirrors the SHToken flaw: the "real" accounting store (`DelegatedResourceStore` / account balances) can diverge from the auxiliary index/list store (`DelegatedResourceAccountIndexStore`) that back the public `GetDelegatedResourceAccountIndex` RPC/HTTP APIs, because an alternate execution path (the TVM native contract path, analogous to `transferFrom()` bypassing custom tracking) does not maintain both data structures consistently.

### Finding Description
There are two independent code paths that implement "freeze/delegate resource" semantics:

1. The normal actuator path, `FreezeBalanceActuator.delegateResource()`, explicitly updates `DelegatedResourceAccountIndexStore` (both legacy list-based and the newer prefix-based V2 index) in addition to `DelegatedResourceStore` and account balances. [1](#0-0) 

2. The TVM native-contract path, `FreezeBalanceProcessor.execute()` / `delegateResource()`, which is exercised when a smart contract invokes the freeze-balance native/precompiled contract functionality from within TVM execution, updates only `DelegatedResourceStore` and the owner/receiver `AccountCapsule` — it never calls into `DelegatedResourceAccountIndexStore`. [2](#0-1) 

By contrast, the newer `DelegateResourceProcessor` (used for `FreezeBalanceV2`-style delegation from TVM) does properly maintain the V2 index store when delegating. [3](#0-2) 

This is structurally the same bug class as SHToken's `userBalances`/`_balances` dual mapping: an authoritative accounting structure (`DelegatedResourceStore`, account balances) and a secondary bookkeeping/index structure (`DelegatedResourceAccountIndexStore`) are supposed to be kept in lockstep, but one code path (`FreezeBalanceProcessor`, reachable via ordinary contract execution/broadcast transactions calling a deployed contract that performs freeze) silently skips updating the index, just like `transferFrom()` skipped `SHToken`'s `users` list update.

### Impact Explanation
The `DelegatedResourceAccountIndexStore` (and its V2 counterpart) back the publicly exposed `GetDelegatedResourceAccountIndex(V2)` gRPC/HTTP/PBFT/Solidity-node APIs. [4](#0-3) 
When delegation occurs via the TVM `FreezeBalanceProcessor` path, real delegated-resource records exist in `DelegatedResourceStore` and are reflected in account balances/weights, but the index used for enumeration/reporting will be missing or stale entries for the owner/receiver pair — producing inaccurate accounting reports and potentially incomplete/incorrect downstream logic that relies on this index (e.g., unfreezing flows in `UnfreezeBalanceActuator`/`UnDelegateResourceProcessor` which read and mutate this same index to locate/clear delegation records). This can misrepresent user relationships and diverge on-chain "accounting view" from the true resource-delegation state, echoing the reported severity in the original SHToken finding (inaccurate accounting/user tracking, not necessarily a fund-theft bug by itself, but a correctness/consistency defect exposed over RPC).

### Likelihood Explanation
The `FreezeBalanceProcessor` path is reachable by any account broadcasting a normal transaction that invokes a deployed smart contract exercising the freeze-balance native contract functionality (gated behind the `allowTvmFreeze`/related VMConfig switches, which appear to already be enabled features in this codebase given active `OperationRegistry`/`Program` wiring). No privileged actor, leaked key, or malicious peer is required — it is triggerable by any unprivileged, anonymous transaction sender able to call a contract that performs freezing/delegation via TVM.

### Recommendation
Update `FreezeBalanceProcessor.delegateResource()` to also update `DelegatedResourceAccountIndexStore` (or its V2 equivalent), mirroring the logic already present in `FreezeBalanceActuator.delegateResource()` and `DelegateResourceProcessor.delegateResource()`. Audit all other TVM native-contract and actuator paths that touch `DelegatedResourceStore`/account delegation fields to ensure `DelegatedResourceAccountIndexStore` is updated symmetrically on every insert/delete, and add regression tests analogous to the SHToken PoC that create a delegation via the TVM freeze path and then assert the index API reflects it.

### Proof of Concept
Conceptual PoC (not executed, derived from code paths above):
1. Deploy a contract that, on invocation, executes the TVM "freeze balance and delegate to receiver" native operation (the path leading into `FreezeBalanceProcessor.execute()` with `param.isDelegating() == true`).
2. Call this contract from an arbitrary account, delegating bandwidth/energy to a receiver address that has no prior delegation relationship.
3. Query `GetDelegatedResourceAccountIndex` (or V2) for the owner/receiver pair via the public HTTP/gRPC API — the index will not list the new delegation, while `DelegatedResourceStore`/account resource fields (queryable via `getaccount` / `getaccountresource`) will correctly show the delegated balance, demonstrating the accounting/index desync.

Note: I could not execute this PoC (no filesystem/terminal access in this mode) and could not fully verify the exact enablement flag/state for `allowTvmFreeze` at runtime in this build; this should be validated on a running node before relying on it as confirmed exploitable.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L319-353)
```java
    //modify DelegatedResourceAccountIndexStore
    if (!dynamicPropertiesStore.supportAllowDelegateOptimization()) {

      DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
          delegatedResourceAccountIndexStore.get(ownerAddress);
      if (ownerIndexCapsule == null) {
        ownerIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(ownerAddress));
      }
      List<ByteString> toAccountsList = ownerIndexCapsule.getToAccountsList();
      if (!toAccountsList.contains(ByteString.copyFrom(receiverAddress))) {
        ownerIndexCapsule.addToAccount(ByteString.copyFrom(receiverAddress));
      }
      delegatedResourceAccountIndexStore.put(ownerAddress, ownerIndexCapsule);

      DelegatedResourceAccountIndexCapsule receiverIndexCapsule
          = delegatedResourceAccountIndexStore.get(receiverAddress);
      if (receiverIndexCapsule == null) {
        receiverIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(receiverAddress));
      }
      List<ByteString> fromAccountsList = receiverIndexCapsule
          .getFromAccountsList();
      if (!fromAccountsList.contains(ByteString.copyFrom(ownerAddress))) {
        receiverIndexCapsule.addFromAccount(ByteString.copyFrom(ownerAddress));
      }
      delegatedResourceAccountIndexStore.put(receiverAddress, receiverIndexCapsule);

    } else {
      // modify DelegatedResourceAccountIndexStore new
      delegatedResourceAccountIndexStore.convert(ownerAddress);
      delegatedResourceAccountIndexStore.convert(receiverAddress);
      delegatedResourceAccountIndexStore.delegate(ownerAddress, receiverAddress,
          dynamicPropertiesStore.getLatestBlockHeaderTimestamp());
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L137-168)
```java
  private void delegateResource(
      byte[] ownerAddress,
      byte[] receiverAddress,
      long frozenBalance,
      long expireTime,
      boolean isBandwidth,
      Repository repo) {
    byte[] key = DelegatedResourceCapsule.createDbKey(ownerAddress, receiverAddress);

    // insert or update DelegateResource
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(
          ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }
    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(frozenBalance, expireTime);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(frozenBalance, expireTime);
    }
    repo.updateDelegatedResource(key, delegatedResourceCapsule);

    // do delegating resource to receiver account
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenBalanceForBandwidth(frozenBalance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenBalanceForEnergy(frozenBalance);
    }
    repo.updateAccount(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L146-191)
```java
  private void delegateResource(
      byte[] ownerAddress,
      byte[] receiverAddress,
      boolean isBandwidth,
      long delegateBalance,
      Repository repo) {
    //modify DelegatedResourceStore
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(
          ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }
    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(delegateBalance, 0);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(delegateBalance, 0);
    }

    //modify DelegatedResourceAccountIndex
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    byte[] fromKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_FROM_PREFIX(), ownerAddress, receiverAddress);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(receiverAddress));
    toIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_TO_PREFIX(), receiverAddress, ownerAddress);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(ownerAddress));
    fromIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(toKey, fromIndexCapsule);

    //update Account for receiver
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(delegateBalance);
    }
    repo.updateDelegatedResource(key, delegatedResourceCapsule);
    repo.updateAccount(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L883-913)
```java
  public DelegatedResourceList getDelegatedResource(ByteString fromAddress, ByteString toAddress) {
    DelegatedResourceList.Builder builder = DelegatedResourceList.newBuilder();
    byte[] dbKey = DelegatedResourceCapsule
        .createDbKey(fromAddress.toByteArray(), toAddress.toByteArray());
    DelegatedResourceCapsule delegatedResourceCapsule = chainBaseManager.getDelegatedResourceStore()
        .get(dbKey);
    if (delegatedResourceCapsule != null) {
      builder.addDelegatedResource(delegatedResourceCapsule.getInstance());
    }
    return builder.build();
  }

  public DelegatedResourceList getDelegatedResourceV2(
          ByteString fromAddress, ByteString toAddress) {
    DelegatedResourceList.Builder builder = DelegatedResourceList.newBuilder();
    byte[] dbKey = DelegatedResourceCapsule
        .createDbKeyV2(fromAddress.toByteArray(), toAddress.toByteArray(), false);
    DelegatedResourceCapsule unlockResource = chainBaseManager.getDelegatedResourceStore()
        .get(dbKey);
    if (nonEmptyResource(unlockResource)) {
      builder.addDelegatedResource(unlockResource.getInstance());
    }
    dbKey = DelegatedResourceCapsule
        .createDbKeyV2(fromAddress.toByteArray(), toAddress.toByteArray(), true);
    DelegatedResourceCapsule lockResource = chainBaseManager.getDelegatedResourceStore()
        .get(dbKey);
    if (nonEmptyResource(lockResource)) {
      builder.addDelegatedResource(lockResource.getInstance());
    }
    return builder.build();
  }
```
