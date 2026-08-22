### Title
Unbounded, unprivileged growth of `DelegatedResourceAccountIndexCapsule` list fields causes O(n) resource-delegation and unfreeze processing on a victim account (Delegate-flood DoS) - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`, `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`, `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java`)

### Summary
The external report describes a `MAX_DELEGATES = 1024` cap that is still too large for fast-block gas limits, letting an attacker flood a victim's delegation list to make the victim's own transfer/withdraw operations unaffordably expensive. java-tron has an analogous, and in fact worse, pattern in its legacy (v1) resource-delegation bookkeeping: `DelegatedResourceAccountIndexCapsule` keeps `toAccountsList`/`fromAccountsList` as unbounded repeated fields with **no size cap at all**, and any address can be forced into another address's list simply by broadcasting a `FreezeBalanceContract` that names the victim as `receiverAddress`.

### Finding Description
`FreezeBalanceActuator.delegateResource()` lets any signer freeze as little as the minimum TRX amount and delegate it to an arbitrary `receiverAddress`, unconditionally appending that address to the owner's `toAccountsList` and appending the owner to the receiver's `fromAccountsList` via `DelegatedResourceAccountIndexCapsule.addToAccount`/`addFromAccount`, with no bound on list length: [1](#0-0) 

These lists live inside a single protobuf-backed capsule value (`DelegatedResourceAccountIndexStore`), so their entire contents are (de)serialized as one blob whenever touched: [2](#0-1) 

When a delegation is later cleared (e.g. by `UnfreezeBalanceActuator`), the code loads the *entire* `toAccountsList`/`fromAccountsList` for both the owner and the receiver into an `ArrayList` and performs a linear scan/remove: [3](#0-2) 

An attacker can trivially inflate a victim's `fromAccountsList` by creating many throwaway accounts and issuing many minimal `FreezeBalanceContract` delegations to the victim. Because there is no cap comparable to the reported `MAX_DELEGATES`, the list can grow far beyond 1024 entries, making any subsequent unfreeze/undelegate transaction that touches the victim's index entry (deserialize the full list, mutate it, re-serialize, write it back) grow linearly with the number of attacker-created delegations, and this cost is paid inside actuator `execute()`/`validate()` with `calcFee()` returning `0`, i.e., the attacker pays only the minimal freeze deposit while inflicting unbounded processing cost on the victim's transaction and on any node executing it.

### Impact Explanation
This is a state-bloat/processing-cost DoS analog reachable purely through broadcast transactions from unprivileged accounts: an attacker can materially increase the block-processing cost (CPU + serialization) required to process a victim's unfreeze/undelegate transaction, and can also make read APIs built on `getIndex()`/`getWithPrefix()` (which merge `prefixQuery` results and sort them) increasingly expensive as the entries accumulate. Unlike the original report where the cap merely needs tightening, here there is no cap in the legacy v1 path at all, so the growth is unbounded by construction.

### Likelihood Explanation
Likelihood is Low/Medium: the newer, hardfork-gated v2 delegation model (`DelegateResourceProcessor`/`DelegatedResourceAccountIndexStore` `V2_FROM_PREFIX`/`V2_TO_PREFIX`) stores each delegation as an independent key rather than in a single growing list, avoiding this problem, and this v1 array-based path is only exercised when `dynamicStore.supportAllowDelegateOptimization()` is false. On networks/deployments where that hard fork parameter has not yet been activated by the committee, the vulnerable legacy path in `FreezeBalanceActuator`/`UnfreezeBalanceActuator`/`DelegatedResourceAccountIndexStore` remains fully reachable from ordinary broadcast transactions at minimal cost to the attacker.

### Recommendation
- Enforce an explicit maximum size on `toAccountsList`/`fromAccountsList` in `DelegatedResourceAccountIndexCapsule`/`DelegatedResourceAccountIndexStore`, rejecting further delegations in `FreezeBalanceActuator.validate()` once a victim's index list reaches the cap.
- Prefer migrating fully to the per-key (v2) delegation index model (as already used for `DelegateResourceContract`) to avoid any single-capsule unbounded list, and consider deprecating/gating off the legacy v1 array-based path entirely regardless of the `supportAllowDelegateOptimization` toggle state.
- Charge fees proportional to the size of the list being mutated (or a per-entry base fee) so the cost of bloating another account's index list is not effectively free for the attacker.

### Proof of Concept
1. Attacker generates N throwaway accounts, each funded with the minimal TRX needed to freeze.
2. For each throwaway account, attacker broadcasts a `FreezeBalanceContract` with `receiverAddress = victim`, invoking `FreezeBalanceActuator.delegateResource()` which appends the throwaway address to `victim`'s `fromAccountsList` with no upper bound: [4](#0-3) 
3. After N is large (well beyond any reasonable cap, e.g., tens of thousands), any transaction that causes `victim`'s delegation entry to be cleared (e.g., an `UnfreezeBalanceContract` from one of the delegators once their freeze expires) forces the node to load and linearly scan/rewrite the full bloated list: [5](#0-4) 
4. Processing cost for that transaction scales with N, degrading block processing time and victim-related account operations, while the attacker's per-delegation cost is only the fixed minimal freeze deposit and a `calcFee()` of `0`.

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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L35-40)
```java
  @Override
  public DelegatedResourceAccountIndexCapsule get(byte[] key) {

    byte[] value = revokingDB.getUnchecked(key);
    return ArrayUtils.isEmpty(value) ? null : new DelegatedResourceAccountIndexCapsule(value);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L163-188)
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
        } else {
          //modify DelegatedResourceAccountIndexStore new
          delegatedResourceAccountIndexStore.convert(ownerAddress);
          delegatedResourceAccountIndexStore.convert(receiverAddress);
          delegatedResourceAccountIndexStore.unDelegate(ownerAddress, receiverAddress);
        }
```
