### Title
Unbounded growth of `DelegatedResourceAccountIndexCapsule` to/from-accounts lists causes escalating-cost DoS on freeze/delegate/unfreeze operations - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`)

### Summary
The legacy (pre-optimization) `DelegatedResourceAccountIndexStore` maintains, per account, a single `DelegatedResourceAccountIndexCapsule` protobuf record holding a `toAccounts` list and a `fromAccounts` list of every distinct address that account has ever delegated resources to/from. Any unprivileged account can grow these lists without bound by repeatedly calling `FreezeBalanceActuator`/`FreezeBalanceContract` with distinct receiver addresses. Every subsequent freeze/unfreeze touching that account deserializes the entire list, does a linear `remove()`, and rewrites the whole list back to storage, exactly analogous to the reported "unbounded iteration over all indexes" pattern in `PoolTemplate.sol`.

### Finding Description
`DelegatedResourceAccountIndexCapsule` stores `toAccounts`/`fromAccounts` as repeated fields in a single protobuf value keyed by account address [1](#0-0) . In the legacy (non-optimized) delegation code path, `FreezeBalanceActuator` adds a new entry to this list every time a user delegates to a new receiver address via `addToAccount`/equivalent index update, with no cap on the number of distinct receiver addresses. `UnfreezeBalanceActuator` then reconstructs the entire list, does a linear scan/removal, and rewrites the whole capsule: [2](#0-1) 

This exact "full list load → linear scan/remove → full list rewrite" pattern means the cost of every freeze/unfreeze operation touching that account grows linearly (or worse, due to protobuf re-serialization of the whole record) with the number of distinct delegation counterparties the account has ever accumulated. Because there is no upper bound enforced on the number of distinct receiver addresses, an attacker fully controls the size of this list by issuing many small `FreezeBalanceContract` delegations to many distinct throwaway addresses, mirroring the unbounded-array root cause described in the external report (an unprivileged actor can inflate a state array that is later iterated/rewritten in full during normal execution).

The `DelegatedResourceAccountIndexStore.convert()` method, used to migrate this legacy per-account list into the newer prefix-keyed (`V2_FROM_PREFIX`/`V2_TO_PREFIX`) scheme, itself iterates the entire legacy `toList`/`fromList` in a for-loop and re-delegates every entry one by one: [3](#0-2) 

If an attacker inflates the legacy list before conversion occurs (i.e., before `supportAllowDelegateOptimization()` is enabled for that account/network state), this migration step becomes proportionally expensive, and it runs inline inside actuator execution (`UnfreezeBalanceActuator`/`FreezeBalanceActuator` call `convert()` on both owner and receiver addresses) rather than being bounded or batched.

### Impact Explanation
- Every account that accumulates a large number of distinct delegation counterparties under the legacy scheme pays an ever-increasing, attacker-influenced cost (CPU + protobuf (de)serialization + DB write size) for basic `FreezeBalanceContract`/`UnfreezeBalanceContract` operations, and for the one-time `convert()` migration triggered from within actuator execution.
- Because the entire list must be loaded and rewritten as a single DB value, the per-transaction cost is not bounded by the fixed `calcFee()`/energy accounting for these actuators, meaning cost growth is effectively unpriced relative to attacker-controlled state size — the same underpriced-unbounded-work class flagged in the original report.
- In the worst case this degrades or blocks the affected account's ability to unfreeze/delegate/undelegate (denial of service for that account's core resource-management functionality), and increases block-processing cost for full nodes replaying such transactions.

### Likelihood Explanation
Reaching this path requires only ordinary, unprivileged `FreezeBalanceContract` calls with many distinct receiver addresses (e.g., attacker-controlled throwaway accounts), which is cheap to execute repeatedly and available to any user. The likelihood of a fully realized, severe on-chain DoS is reduced because:
- The current/optimized delegation model (`supportAllowDelegateOptimization()` true, i.e., `DelegateResourceActuator`/`UnDelegateResourceActuator` using `delegateV2`/`unDelegateV2` with prefix-keyed per-pair storage) is not vulnerable to this pattern — each delegation pair is stored/queried independently rather than in one growing list.
- This makes the vulnerable code reachable primarily via the legacy `FreezeBalanceContract`/`UnfreezeBalanceContract` path, which is superseded by the V2 model on networks/accounts where the optimization flag is active. I was not able to fully confirm from the indexed code whether the legacy contract type remains callable on current mainnet configuration or is fully disabled once the optimization hard fork is active — this is a real limitation of my analysis given index coverage, and would need to be confirmed by directly checking `ProposalUtil`/`ForkController` gating for `FreezeBalanceContract` and the default value of `getAllowDelegateOptimization()` in production configs.

### Recommendation
- Enforce a maximum number of distinct delegation counterparties tracked per account in the legacy `DelegatedResourceAccountIndexCapsule` (mirroring the cap already present for market orders, e.g. `MarketSellAssetActuator`'s 100-order limit), or disable/reject new legacy `FreezeBalanceContract` delegations entirely once the V2 optimized model is active.
- If the legacy path must remain reachable for backward compatibility, migrate any account's legacy index to the V2 prefixed scheme lazily and incrementally (bounded per call) rather than performing the full `convert()` synchronously inside `UnfreezeBalanceActuator`/`FreezeBalanceActuator` execution.
- Price the cost of `UnfreezeBalanceActuator`'s list scan/rewrite and `DelegatedResourceAccountIndexStore.convert()` proportionally to list size in the actuator's fee/energy accounting, so the cost is not effectively free/underpriced relative to attacker-controlled state size.

### Proof of Concept
1. Ensure the network/account state has `supportAllowDelegateOptimization()` disabled (legacy path active).
2. From account A, repeatedly submit `FreezeBalanceContract` transactions delegating a minimal amount of bandwidth/energy to N distinct, attacker-controlled receiver addresses (`R1..RN`), each triggering `delegateResourceAccountIndexStore` updates that append to A's `toAccounts` list (and each `Ri`'s `fromAccounts` list).
3. Observe that A's `DelegatedResourceAccountIndexCapsule` (`toAccounts`) grows to size N with no cap enforced.
4. Submit an `UnfreezeBalanceContract` transaction from A for any one delegation; observe execution reconstructing/rewriting the full N-sized list via [2](#0-1) , with cost scaling with N — as N grows large (e.g., tens of thousands), this operation's cost grows correspondingly, degrading or threatening to exceed practical processing limits for that account's subsequent freeze/unfreeze/delegate operations, analogous to the unbounded-array DoS in the referenced `PoolTemplate.sol` report.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java (L82-94)
```java
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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L42-61)
```java
  public void convert(byte[] address) {
    DelegatedResourceAccountIndexCapsule indexCapsule = this.get(address);
    if (indexCapsule == null) {
      // convert complete or have no delegate
      return;
    }
    // convert old data
    List<ByteString> toList = indexCapsule.getToAccountsList();
    for (int i = 0; i < toList.size(); i++) {
      // use index as the timestamp, just to keep index in order
      this.delegate(address, toList.get(i).toByteArray(), i + 1L);
    }

    List<ByteString> fromList = indexCapsule.getFromAccountsList();
    for (int i = 0; i < fromList.size(); i++) {
      // use index as the timestamp, just to keep index in order
      this.delegate(fromList.get(i).toByteArray(), address, i + 1L);
    }
    this.delete(address);
  }
```
