### Title
Unbounded rewrite of `DelegatedResourceAccountIndexCapsule` accounts list on every delegation removal - ([File: chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java])

### Summary
The `_removeFromQueue` bug class (O(n) full-array rewrite for removing a single element from a queue/list) maps to the legacy `DelegatedResourceAccountIndexCapsule.toAccountsList` / `fromAccountsList` maintenance code in java-tron. Whenever a single delegation entry is removed from these lists, the entire list is copied, mutated, and rewritten to storage as a whole, even though only one entry changed.

### Finding Description
`DelegatedResourceAccountIndexCapsule` stores an unbounded, attacker-growable list of delegatee/delegator addresses per account: [1](#0-0) 

`removeToAccount`/`removeFromAccount` and `setAllToAccounts`/`setAllFromAccounts` implement removal by copying the whole list into an `ArrayList`, removing the single target element, then calling `clearToAccounts().addAllToAccounts(list)` — i.e. rewriting every remaining element into the protobuf message on each removal: [2](#0-1) 

This same full-list-copy-and-rewrite pattern is inlined directly in the legacy `UnfreezeBalanceActuator.execute()` path, guarded by `!dynamicStore.supportAllowDelegateOptimization()`: [3](#0-2) 

The list itself is grown unbounded by `FreezeBalanceActuator.delegateResource()`, which appends a new receiver address to `toAccountsList`/`fromAccountsList` for every distinct delegatee, with no cap on the number of distinct delegate relationships an account can create: [4](#0-3) 

The existing test suite explicitly exercises and validates this quadratic-cost pattern: 100 pre-existing entries are stored, and a single delegation operation reads/copies/rewrites the whole 100+ element list: [5](#0-4) 

This is structurally the same defect the external report describes for `PolicyBook._removeFromQueue`: removing a single logical entry forces an O(n) rewrite of the entire backing collection instead of only touching the changed slot, because the data structure (a single repeated protobuf field keyed by one address) has no notion of independently addressable, append/delete-only indices.

### Impact Explanation
Each `unfreezeBalance`/legacy-unfreeze operation that clears out a delegation relation triggers an O(n) copy + O(n) protobuf re-serialization + full key-value rewrite of the requesting account's (and the counterparty's) `DelegatedResourceAccountIndex` record, where n is the total number of distinct delegate relationships the account has ever created via the legacy (non-optimized) resource-delegation model. Because delegation targets are attacker-chosen distinct addresses (no reuse required) and `FreezeBalanceActuator` does not cap the number of distinct receivers, an account can grow this list arbitrarily large over many prior freeze/delegate transactions. Any single subsequent unfreeze of one of those delegations forces the node to do work proportional to the full accumulated list size, while the fee charged for the operation does not scale with list size (`calcFee()` for this actuator is fixed, independent of the size of the account's delegation-index list). This is a resource-cost/fee mismatch: an attacker can cheaply build up an oversized index over time and then trigger disproportionately expensive processing on unfreeze, degrading validator/node CPU and I/O during block processing.

### Likelihood Explanation
This code path is only active for accounts whose chain state predates or otherwise falls under the legacy (`!supportAllowDelegateOptimization()`) resource-delegation model. Whether this flag is enabled by default on current mainnet, and whether new delegation index entries can still be created via this legacy code path at HEAD, is uncertain — I could not fully confirm the current on-chain/default value of `AllowDelegateOptimization` from the available index, only that the proposal-controlled flag exists in `DynamicPropertiesStore` and is checked at these call sites. If the optimization is enabled network-wide and irreversible, this legacy path may be dormant for new activity but could still be triggered by historical accounts that accumulated large lists before the optimization was activated (their lists are lazily migrated via `DelegatedResourceAccountIndexStore.convert()` only when touched). This uncertainty should be resolved by checking the proposal/parameter's current activation state and whether `convert()` is invoked eagerly enough to prevent large legacy lists from persisting.

### Recommendation
Replace the single "list of all delegatees/delegators" repeated field with individually addressable keys (which the codebase has already partially done via `DelegatedResourceAccountIndexStore.delegate()`/`unDelegate()` using `FROM_PREFIX`/`TO_PREFIX` + address as key, i.e., the "optimized" model). Ensure this optimized model is the only path reachable for both creation and removal, and proactively convert/migrate legacy large lists (via `convert()`) rather than continuing to service removals against the flat list representation, so that removal of a single delegation is O(1) rather than O(n).

### Proof of Concept
1. Attacker account `A` freezes a small balance and delegates it to `N` distinct freshly generated receiver addresses `R_1..R_N` using `FreezeBalanceContract` with `receiver_address` set differently each time, while `supportAllowDelegateOptimization()` is false for `A`'s existing record (as exercised in the test `testMultiFreezeDelegatedBalanceForBandwidth`, which sets up 100 pre-existing entries) — see [6](#0-5) . Each delegate call appends to `ownerIndexCapsule.toAccountsList` via `addToAccount` in `FreezeBalanceActuator.delegateResource()` — [7](#0-6) .
2. `A` then submits an `UnfreezeBalanceContract` for any single one of the `N` delegations.
3. `UnfreezeBalanceActuator.execute()` copies the entire `toAccountsList`/`fromAccountsList` (size `N`) into new `ArrayList`s, removes one entry, and calls `setAllToAccounts`/`setAllFromAccounts`, which clears and rewrites the whole repeated field before persisting the capsule — [8](#0-7) .
4. Repeating steps 1–3 for increasing `N` demonstrates unbounded, attacker-controlled linear cost per unfreeze operation with no corresponding fee increase, since `calcFee()` in these legacy actuators is not indexed to list size.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java (L50-94)
```java
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

**File:** framework/src/test/java/org/tron/core/actuator/FreezeBalanceActuatorTest.java (L275-296)
```java
  @Test
  public void testMultiFreezeDelegatedBalanceForBandwidth() {
    dbManager.getDynamicPropertiesStore().saveAllowDelegateResource(1);
    dbManager.getDynamicPropertiesStore().saveAllowDelegateOptimization(1L);
    dbManager.getDynamicPropertiesStore().saveLatestBlockHeaderTimestamp(10000L);
    long frozenBalance = 1_000_000_000L;
    long duration = 3;
    final int RECEIVE_COUNT = 100;
    String[] RECEIVE_ADDRESSES = new String[RECEIVE_COUNT + 1];

    DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS)));
    for (int i = 0; i < RECEIVE_COUNT + 1; i++) {
      ECKey ecKey = new ECKey(Utils.getRandom());
      RECEIVE_ADDRESSES[i] = ByteArray.toHexString(ecKey.getAddress());
      if (i != RECEIVE_COUNT) {
        ownerIndexCapsule.addToAccount(ByteString.copyFrom(ecKey.getAddress()));
      }
    }
    dbManager.getDelegatedResourceAccountIndexStore().put(
        ByteArray.fromHexString(OWNER_ADDRESS), ownerIndexCapsule);
```
