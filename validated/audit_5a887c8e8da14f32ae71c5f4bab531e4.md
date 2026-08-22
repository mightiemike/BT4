### Title
Unbounded growth of `DelegatedResourceAccountIndexCapsule.fromAccountsList`/`toAccountsList` causes O(n) contains/remove DoS on freeze/delegate/unfreeze actuators - (File: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`)

### Summary
When the `allowDelegateOptimization` committee flag is not active, `FreezeBalanceActuator.delegateResource` and `UnfreezeBalanceActuator` maintain resource-delegation indices as plain in-protobuf `List<ByteString>` fields (`toAccountsList` / `fromAccountsList`) on `DelegatedResourceAccountIndexCapsule`, mutated with `.contains()` and `.remove()` calls. Nothing caps the size of these lists, so any account can cheaply grow another account's `fromAccountsList` (as the recipient of many small delegations from many distinct sender addresses), after which every future delegate/unfreeze transaction touching that account pays an O(n) traversal cost — directly analogous to the Axis-Finance EMPAM bug where an attacker inflates a shared bid list and later victim operations must scan the whole list.

### Finding Description
`FreezeBalanceActuator.delegateResource` (legacy, non-optimized path): [1](#0-0) 
performs, for every `FreezeBalanceContract` with a receiver address:
```
List<ByteString> toAccountsList = ownerIndexCapsule.getToAccountsList();
if (!toAccountsList.contains(ByteString.copyFrom(receiverAddress))) {
  ownerIndexCapsule.addToAccount(...)
}
...
List<ByteString> fromAccountsList = receiverIndexCapsule.getFromAccountsList();
if (!fromAccountsList.contains(ByteString.copyFrom(ownerAddress))) {
  receiverIndexCapsule.addFromAccount(...)
}
```
Both `toAccountsList` and `fromAccountsList` are unbounded `List<ByteString>` fields inside a single protobuf-backed capsule (`DelegatedResourceAccountIndexCapsule`), read/written/rewritten in full on every call: [2](#0-1) 

On the withdraw/unfreeze side, `UnfreezeBalanceActuator` copies the whole list into a new `ArrayList`, then calls `.remove(...)` — another O(n) linear scan/rewrite of the entire list: [3](#0-2) 

Because a single account (the receiver) can be the delegation target of arbitrarily many distinct owner addresses, an attacker controlling many funded addresses can repeatedly send cheap `FreezeBalanceContract` transactions (minimum freeze amount, `BANDWIDTH` resource, tiny lock durations) that each delegate resources to the same victim receiver address. Each such transaction appends one entry to `receiverIndexCapsule.fromAccountsList` with no upper bound check, unlike the `MAX_VOTE_NUMBER`/`MAX_MATCH_NUM` caps enforced elsewhere in the codebase (e.g., `VoteWitnessActuator`, `MarketSellAssetActuator`). Once the list is large, any subsequent `.contains()` or `.remove()` call against that same list (triggered by any owner, including the victim's own future freeze/unfreeze/delegate operations, or by any other attacker address delegating to the same receiver) becomes an O(n) operation, and the whole capsule (including the bloated list) must be deserialized/reserialized on every touch.

This exactly mirrors the reported bug class: a shared, attacker-growable list that other (unprivileged) participants' unrelated operations must fully traverse/mutate, degrading or blocking their transactions — the same "loop through all bids to find one" DoS pattern described in the Sherlock report, here manifesting as "loop/copy through all delegation index entries to find one."

### Impact Explanation
An attacker can, with a modest but bounded amount of TRX distributed across many self-controlled accounts, inflate the `fromAccountsList`/`toAccountsList` of a chosen victim account to an arbitrary size. Once large enough, this:
- increases CPU and block-processing time for every future `FreezeBalanceContract`/`UnfreezeBalanceContract` transaction interacting with that account (list `.contains()` and `.remove()` scans, plus repeated protobuf ser/deser of an ever-growing message),
- can degrade the victim's own or third parties' ability to freeze/delegate/unfreeze resources involving that receiver address within block-processing time budgets, and
- represents an unbounded state-growth vector with no economic cap tied to the cost of the operation, since freeze/delegate fees do not scale with index-list size.

This is a resource-accounting/DoS class impact reachable purely from anonymous, broadcast transactions (no special privileges needed), matching the "DoS via protocol implementation" acceptance criterion.

### Likelihood Explanation
Likelihood is bounded by one important caveat I could not fully resolve: this vulnerable code path is only exercised when the on-chain committee proposal `allowDelegateOptimization` (`ALLOW_DELEGATE_OPTIMIZATION`) has NOT been enabled — the optimized path (`delegatedResourceAccountIndexStore.delegate`/`convert`, prefix-keyed rather than list-based) is used once that flag is active: [4](#0-3) 
On java-tron mainnet this proposal has historically been activated, which would make the list-based path effectively legacy/dead code for `FreezeBalanceActuator`/`UnfreezeBalanceActuator` in the current chain state. However, the vulnerable code remains present and reachable on any network/deployment where that flag is not yet set (new private chains, test networks, or chains where the proposal has not activated), and the flag's activation state is a governance/config parameter outside code guarantees. I was unable to verify the current default/activation state definitively from the indexed code alone.

### Recommendation
- Enforce a hard cap (mirroring `MAX_VOTE_NUMBER` in `VoteWitnessActuator` or `MAX_MATCH_NUM` in `MarketSellAssetActuator`) on the number of entries permitted in `toAccountsList`/`fromAccountsList` before allowing a new delegation, rejecting the transaction with `ContractValidateException` once the cap is reached.
- Regardless of the `allowDelegateOptimization` flag's current activation state, remove or fully retire the unbounded list-based legacy path, or migrate all remaining callers unconditionally to the prefix-keyed `DelegatedResourceAccountIndexStore.delegate`/`unDelegate`/`delegateV2`/`unDelegateV2` methods, which use O(1) keyed writes instead of O(n) list scans — eliminating the shared unbounded array entirely, consistent with the original bug's recommended fix (index/position tracking instead of full-list iteration).

### Proof of Concept
1. Deploy/observe a java-tron network where `allowDelegateOptimization` is not yet enabled (default state before the corresponding committee proposal is passed).
2. Attacker generates N distinct funded addresses `A_1..A_N`.
3. For each `A_i`, broadcast a `FreezeBalanceContract` with `resource=BANDWIDTH`, minimal `frozen_balance`, and `receiver_address = Victim`, invoking `FreezeBalanceActuator.execute` → `delegateResource`, which appends `A_i` to `Victim`'s `DelegatedResourceAccountIndexCapsule.fromAccountsList` via `receiverIndexCapsule.addFromAccount(...)` with no size check.
4. After N is large (e.g., tens of thousands), any subsequent `FreezeBalanceContract`/`UnfreezeBalanceContract` transaction touching `Victim`'s index (from `Victim` itself, or from any of `A_1..A_N` unfreezing) triggers `.contains()`/`.remove()` over the full N-element list plus full capsule re-serialization, materially increasing per-transaction processing cost/time versus the un-inflated baseline — the same "attacker inflates a shared list, victim pays O(n) on later withdrawal" pattern as the referenced EMPAM `refundBid` bug.

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
