### Title
Unbounded delegated-resource index arrays cause O(n) storage rewrite on every delegate/unfreeze operation - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java])

### Summary
The legacy (non-optimized) `DelegatedResourceAccountIndexStore` bookkeeping path stores every distinct delegatee/delegator address a user has ever interacted with inside a single protobuf-backed `toAccountsList` / `fromAccountsList` on `DelegatedResourceAccountIndexCapsule`. This list has no maximum size enforced anywhere, and every delegate or unfreeze transaction fully deserializes, scans, mutates, and re-serializes the whole list — mirroring the reported MultiFeeDistribution.sol pattern of "iteration through the whole storage array" on withdrawal.

### Finding Description
When `dynamicStore.supportAllowDelegateOptimization()` is `false` (the legacy/default-off path until a committee proposal enables the optimized index), `FreezeBalanceActuator.delegateResource()` maintains `DelegatedResourceAccountIndexCapsule` records: [1](#0-0) 

Each new distinct receiver address a user delegates to is appended to `toAccountsList` (and symmetrically `fromAccountsList` on the receiver) with only a `contains()` check to avoid duplicates — there is no cap on the number of distinct receivers: [2](#0-1) 

The capsule itself provides no bound either — it simply wraps a repeated protobuf field: [3](#0-2) 

Critically, when the delegation is later fully unwound via `UnfreezeBalanceActuator`, the code copies the **entire list** into a new `ArrayList`, removes a single element, and writes the entire list back to the store: [4](#0-3) 

This is the same anti-pattern flagged in the external report: an unbounded per-account storage array that must be fully materialized, scanned and rewritten on every state-mutating operation, rather than performing a bounded, keyed lookup/removal.

An attacker (or even an ordinary user, since this requires no privileged role) can grow this array arbitrarily by repeatedly calling `FreezeBalanceContract` with `delegated resource = true` targeting a large number of distinct freshly-created receiver addresses, each with the network's minimum freezable amount. Each such call is a normal broadcast transaction and only costs a fixed bandwidth/energy fee — the cost is not scaled to the size of the resulting index array, so the attacker pays a constant fee per entry while the actual work (list copy + protobuf re-serialization + `contains`/`remove` scans, both O(n)) grows linearly with the number of entries already present.

### Impact Explanation
- Every subsequent freeze, delegate, or unfreeze transaction touching the bloated account (whether by the attacker or an unrelated legitimate account they’ve delegated to/from) must pay to fully deserialize, linear-scan, and rewrite this ever-growing repeated field, while the transaction's declared bandwidth/energy fee remains flat.
- This creates an accounting/DoS mismatch: real CPU and I/O cost during block processing is not proportional to the resource fee charged, allowing an actor to impose disproportionate processing burden on the node that must execute/validate the block containing such a transaction, and potentially on all subsequent unfreeze operations touching the bloated account.
- In the worst case, as flagged by the source report analog, if the per-transaction work required to unwind the array grows large enough, the account's own `unfreezeBalance` calls involving these entries could become disproportionately expensive relative to their resource budget, echoing the "funds get stuck" scenario from the original finding (although java-tron enforces block-level rather than per-tx gas limits, so the primary risk here is node-level CPU/DoS rather than fund lock-up).

### Likelihood Explanation
Medium. The vulnerable path is gated behind `dynamicStore.supportAllowDelegateOptimization()` being `false`, which is the state prior to the relevant committee proposal being passed on a given network; on networks/testnets where that proposal has not (yet) been enacted, the legacy code path is fully reachable via ordinary `FreezeBalanceContract`/`UnfreezeBalanceContract` transactions from any account, with no special privileges required, only the cost of the frozen amount itself (which can be minimized to the protocol minimum).

### Recommendation
- Enforce a maximum size on `DelegatedResourceAccountIndexCapsule.toAccountsList` / `fromAccountsList` in the legacy path, rejecting further delegations once the cap is reached (mirroring the post-audit fix noted in the external report for `MultiFeeDistribution`).
- Prefer/force migration to the already-implemented "optimized" indexing scheme (`delegatedResourceAccountIndexStore.delegate`/`convert`/`unDelegate`, keyed by `(from,to)` pairs rather than an in-place list) for all networks, since that store avoids the full-list read/rewrite pattern.
- If the legacy path must remain supported for backward compatibility, charge additional bandwidth/energy proportional to the current size of the index list being rewritten.

### Proof of Concept
1. On a network where `AllowDelegateOptimization` has not been enabled (`supportAllowDelegateOptimization() == false`), have account A repeatedly broadcast `FreezeBalanceContract` transactions with `resource=BANDWIDTH`, `receiver_address` = a new randomly generated address each time, minimal `frozen_balance`.
2. Each call appends one entry to A's `toAccountsList` (and the corresponding receiver's `fromAccountsList`) with no upper bound, per `FreezeBalanceActuator.delegateResource()` lines 319-345.
3. Repeat thousands of times to grow the list.
4. Broadcast an `UnfreezeBalanceContract` for A that triggers the "no remaining frozen balance" branch in `UnfreezeBalanceActuator` (lines 158-188); observe that the actuator copies the entire `toAccountsList`, performs `remove()`, and rewrites the whole capsule — CPU/time cost scales with the number of entries created in step 1, while the transaction's declared fee/bandwidth cost stays constant.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L319-345)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java (L46-94)
```java
  public List<ByteString> getFromAccountsList() {
    return this.delegatedResourceAccountIndex.getFromAccountsList();
  }

  public void setAllFromAccounts(List<ByteString> fromAccounts) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .clearFromAccounts()
        .addAllFromAccounts(fromAccounts)
        .build();
  }

  public void addFromAccount(ByteString fromAccount) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .addFromAccounts(fromAccount)
        .build();
  }

  public void removeFromAccount(ByteString fromAccount) {
    if (getFromAccountsList().contains(fromAccount)) {
      List<ByteString> fromList = new ArrayList<>(getFromAccountsList());
      fromList.remove(fromAccount);
      setAllFromAccounts(fromList);
    }
  }

  public List<ByteString> getToAccountsList() {
    return this.delegatedResourceAccountIndex.getToAccountsList();
  }

  public void setAllToAccounts(List<ByteString> toAccounts) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .clearToAccounts()
        .addAllToAccounts(toAccounts)
        .build();
  }

  public void addToAccount(ByteString toAccount) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .addToAccounts(toAccount)
        .build();
  }

  public void removeToAccount(ByteString toAccount) {
    if (getToAccountsList().contains(toAccount)) {
      List<ByteString> toList = new ArrayList<>(getToAccountsList());
      toList.remove(toAccount);
      setAllToAccounts(toList);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L162-188)
```java
        //modify DelegatedResourceAccountIndexStore
        if (!dynamicStore.supportAllowDelegateOptimization()) {
          DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
              delegatedResourceAccountIndexStore.get(ownerAddress);
          if (ownerIndexCapsule != null) {
            List<ByteString> toAccountsList = new ArrayList<>(ownerIndexCapsule
                .getToAccountsList());
            toAccountsList.remove(ByteString.copyFrom(receiverAddress));
            ownerIndexCapsule.setAllToAccounts(toAccountsList);
            delegatedResourceAccountIndexStore.put(ownerAddress, ownerIndexCapsule);
          }

          DelegatedResourceAccountIndexCapsule receiverIndexCapsule =
              delegatedResourceAccountIndexStore.get(receiverAddress);
          if (receiverIndexCapsule != null) {
            List<ByteString> fromAccountsList = new ArrayList<>(receiverIndexCapsule
                .getFromAccountsList());
            fromAccountsList.remove(ByteString.copyFrom(ownerAddress));
            receiverIndexCapsule.setAllFromAccounts(fromAccountsList);
            delegatedResourceAccountIndexStore.put(receiverAddress, receiverIndexCapsule);
          }
        } else {
          //modify DelegatedResourceAccountIndexStore new
          delegatedResourceAccountIndexStore.convert(ownerAddress);
          delegatedResourceAccountIndexStore.convert(receiverAddress);
          delegatedResourceAccountIndexStore.unDelegate(ownerAddress, receiverAddress);
        }
```
