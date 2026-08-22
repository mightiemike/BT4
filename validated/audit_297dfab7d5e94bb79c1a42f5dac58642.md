## Title
Unbounded array growth with O(n) linear search/rebuild in `DelegatedResourceAccountIndexCapsule` toAccountsList/fromAccountsList (legacy delegation index path) - (File: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java`)

### Summary
The legacy (pre-`AllowDelegateOptimization`) delegated-resource indexing path stores every distinct delegation counterparty address in a protobuf-backed `List<ByteString>` (`toAccountsList` / `fromAccountsList`) that only grows and is never bounded, while both membership checks and removal are done via linear `List.contains()`/`List.remove()`, and every mutation rewrites the *entire* list back into the capsule. This is the same bug class as the Frax `CrossChainCanonical.sol` `minters_array`/`bridge_tokens_array` issue: an attacker-growable array that is linearly searched/rewritten, with the operation cost proportional to array size.

### Finding Description
`DelegatedResourceAccountIndexCapsule` exposes: [1](#0-0) 

`addToAccount`/`addFromAccount` append to the underlying protobuf-repeated field, and `removeFromAccount`/`removeToAccount` do `getFromAccountsList().contains(...)` then `new ArrayList<>(list); list.remove(x); setAllFromAccounts(list)` which does `clearFromAccounts().addAllFromAccounts(fromAccounts)` — an O(n) full rebuild of the field on every single removal: [2](#0-1) 

This capsule is populated and mutated directly from ordinary user-broadcast transactions in `FreezeBalanceActuator.delegateResource` (guarded only by `!dynamicPropertiesStore.supportAllowDelegateOptimization()`): [3](#0-2) 

and unwound in `UnfreezeBalanceActuator.execute`, which reads the whole list, does a linear `List.remove()`, and writes the whole list back: [4](#0-3) 

An attacker fully controls the fan-out: by repeatedly issuing `FreezeBalanceContract` with `delegated=true` to N distinct fresh receiver addresses (or having N distinct accounts delegate to one victim owner), the owner's `toAccountsList` (or the receiver's `fromAccountsList`) grows to size N with no upper bound enforced anywhere in `FreezeBalanceActuator.validate()`/`execute()`. Every subsequent `UnfreezeBalanceActuator` execution against that owner/receiver pair must then linearly scan and fully rewrite this ever-growing list as part of ordinary transaction execution (which counts toward block processing, i.e. consensus-critical work), exactly mirroring the reported Solidity pattern where minters_array is pushed-to unboundedly and linearly searched on removal.

### Impact Explanation
As the index list grows, the per-transaction cost of unfreezing (calling `UnfreezeBalanceActuator`) against that account grows linearly with the number of distinct delegation counterparties, since each call does a `List.contains`/`List.remove` and a full re-serialization of the repeated protobuf field back into the capsule and then into the underlying store. Because this executes during ordinary block processing (not gas-metered TVM execution but still bounded by processing-time/resource expectations of a fixed block interval), an attacker can force a target owner/receiver account's delegation index to grow arbitrarily large, degrading the processing cost of every future freeze/unfreeze/delegate operation touching that account and increasing block-processing/validation time for all nodes that must re-execute or re-validate the same transaction. This is a resource-exhaustion / DoS-oriented pattern reachable from anonymous broadcast transactions, not privileged-actor or node-operator action.

### Likelihood Explanation
`FreezeBalanceContract` with `delegated=true` targeting distinct receivers is a normal, unprivileged transaction type available to any account, and there is no cap in `FreezeBalanceActuator.validate()` on the number of distinct receivers an owner can delegate to or the number of distinct owners that can delegate to one receiver. The vulnerable path is gated behind `!dynamicPropertiesStore.supportAllowDelegateOptimization()`, i.e., it is only exploitable on networks/time windows where the `AllowDelegateOptimization` proposal has not been activated by the committee — this includes any private/custom java-tron deployment that never enables the proposal, or the historical window on public chains before activation.

### Recommendation
Enforce a hard cap on the number of entries in `toAccountsList`/`fromAccountsList` in `FreezeBalanceActuator.validate()` (and reject further delegation once the cap is reached), or eliminate reliance on this linearly-scanned, fully-rewritten list altogether by keying the index directly (as the `V2`/`AllowDelegateOptimization` prefix-based store already does), and treat this as the default/only implementation rather than a config-gated migration path. As a broader remediation, add the CI-level check recommended by the Frax report — flag any pattern that both appends to a `List`/repeated field without bound and separately performs `List.contains`/`List.remove`/full-rewrite on it — to prevent regressions in the codebase.

### Proof of Concept
1. Deploy/operate a java-tron network where `AllowDelegateOptimization` proposal (`supportAllowDelegateOptimization()`) has not been enabled by the committee (default off).
2. From account `V` (victim/receiver), have `N` distinct funded accounts each submit a `FreezeBalanceContract` transaction with `delegated=true` and `receiver_address = V`, each for a small amount. This is processed by `FreezeBalanceActuator.delegateResource`, and each call appends the sender to `V`'s `fromAccountsList` via `receiverIndexCapsule.addFromAccount(...)` since the `contains` check only prevents duplicates from the *same* sender. [5](#0-4) 
3. Repeat for large `N` (bounded only by available accounts/fees, not by any protocol limit) to grow `V`'s `fromAccountsList` to size `N`.
4. Observe that every subsequent `UnfreezeBalanceActuator` execution touching `V`'s delegated resources triggers a `contains`/`remove` scan and a full list rebuild proportional to `N`: [4](#0-3) 
5. As `N` grows, the cost of processing ordinary unfreeze transactions against `V` grows linearly and unboundedly, matching the Frax `minters_array` DoS pattern.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L163-182)
```java
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
```
