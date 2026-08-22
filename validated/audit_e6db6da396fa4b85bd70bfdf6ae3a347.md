### Title
Unbounded growth of `DelegatedResourceAccountIndexCapsule.fromAccountsList`/`toAccountsList` lets anyone grief a victim's resource-delegation index at low cost - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java])

### Summary
In the legacy (pre-`AllowDelegateOptimization`) delegate-resource path, any account can freeze a small balance and delegate it to an arbitrary receiver address that it does not own. Each such call appends an entry to the receiver's `fromAccountsList` (and the caller's own `toAccountsList`) inside a single `DelegatedResourceAccountIndexCapsule` value that is stored as one serialized protobuf object per account, not as individually-keyed rows. Because the entire list is loaded fully into memory on every subsequent access, an attacker can cheaply inflate a victim's index list and make later delegate/undelegate operations involving that victim increasingly expensive - the same "cheap write, expensive later read" DoS pattern described in the external report.

### Finding Description
`FreezeBalanceActuator.delegateResource()` handles the legacy (non-V2) delegation bookkeeping. When `!dynamicPropertiesStore.supportAllowDelegateOptimization()`, it loads the full `DelegatedResourceAccountIndexCapsule` for both the owner and the receiver, appends the counterpart address to the in-memory `List<ByteString>`, and re-serializes/stores the whole object: [1](#0-0) 

The receiver address is fully attacker-controlled - any account can call `FreezeBalance` with itself as owner and any other account's address as the `receiverAddress`, so the attacker never needs the victim's key to grow the victim's `fromAccountsList`: [2](#0-1) 

`DelegatedResourceAccountIndexCapsule` stores these as plain repeated protobuf fields, and every mutation (`addToAccount`, `addFromAccount`, `removeFromAccount`, `removeToAccount`) round-trips through `toBuilder()`/rebuild of the entire list: [3](#0-2) 

The store's `getIndex()` (used for legacy lookups) also materializes the whole capsule from storage, and `get(address)` is a full-value deserialization - both are on the hot path for any subsequent freeze/unfreeze delegate action involving the victim's account: [4](#0-3) 

This mirrors the reported bug class exactly: an unprivileged caller can target an arbitrary victim address as a parameter to a public entry point, causing an unbounded array tied to that victim to grow in storage; later, any operation that needs to read/modify that array (analogous to `requestSubAccount`) must load and rewrite the entire array, so cost scales with attacker-controlled list size rather than with legitimate usage.

### Impact Explanation
An attacker can repeatedly call `FreezeBalanceContract` with minimal frozen balance and a chosen victim as receiver, growing the victim's `DelegatedResourceAccountIndexCapsule.fromAccountsList` arbitrarily. Every future legitimate freeze/delegate or unfreeze/undelegate operation touching that victim account (owner or receiver side) then has to deserialize, scan (`contains`/`remove`), and re-serialize the ever-growing list, inflating both computation and storage-write size for that transaction. This can materially raise the resource cost of victims' subsequent operations and, at scale, degrade node processing time for blocks containing such transactions - a DoS/griefing vector directly analogous to the reported `deploySpareSubAccount` issue.

### Likelihood Explanation
The path is only exercised when `DynamicPropertiesStore.supportAllowDelegateOptimization()` is false, i.e., on chains/networks where the `AllowDelegateOptimization` proposal has not been enabled by committee vote (it is a governance-toggled parameter referenced in `ProposalUtil`/`ProposalService`). On networks where this optimization has already been activated (which appears to be the direction most maintained chains take, given the newer `DelegateResourceProcessor`/`UnDelegateResourceProcessor` use per-pair prefixed keys instead of growable arrays), this specific legacy code path is not reachable. I was unable to confirm from the indexed code whether this parameter's default is enabled or disabled on any particular deployed network, so likelihood is network/configuration-dependent and could not be fully verified from the available index.

### Recommendation
- Enable (or make default) `AllowDelegateOptimization` so the legacy array-based `DelegatedResourceAccountIndexCapsule` path is fully retired in favor of the prefix-keyed V2 scheme already implemented in `DelegatedResourceAccountIndexStore.delegateV2`/`unDelegateV2` and `DelegateResourceProcessor`/`UnDelegateResourceProcessor`.
- If the legacy path must remain reachable for backward compatibility, cap the number of entries stored in `toAccountsList`/`fromAccountsList` per account, or migrate reads/writes to per-pair keys (as V2 already does) so no single transaction needs to load/rewrite an unbounded list.

### Proof of Concept
1. On a network where `supportAllowDelegateOptimization()` is false, attacker account A freezes a minimal amount of TRX (e.g., 1,000,000 sun) via `FreezeBalanceContract`, specifying `receiver_address = victim`.
2. `FreezeBalanceActuator.delegateResource` loads victim's `DelegatedResourceAccountIndexCapsule`, appends A's address to `fromAccountsList`, and persists the full object.
3. Repeat step 1 with N distinct throwaway attacker accounts, all targeting the same victim, to grow `fromAccountsList` to hundreds/thousands of entries at minimal cost per call.
4. Any subsequent freeze/delegate or unfreeze/undelegate transaction involving the victim now must fully deserialize/scan/rewrite the bloated list in `FreezeBalanceActuator`/`UnfreezeBalanceActuator`, increasing that transaction's resource cost proportional to N. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L289-299)
```java
  private long delegateResource(byte[] ownerAddress, byte[] receiverAddress, boolean isBandwidth,
      long balance, long expireTime) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicPropertiesStore = chainBaseManager.getDynamicPropertiesStore();
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    DelegatedResourceAccountIndexStore delegatedResourceAccountIndexStore = chainBaseManager
        .getDelegatedResourceAccountIndexStore();
    byte[] key = DelegatedResourceCapsule.createDbKey(ownerAddress, receiverAddress);
    //modify DelegatedResourceStore
    DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore
        .get(key);
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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L106-112)
```java
  public DelegatedResourceAccountIndexCapsule getIndex(byte[] address) {
    DelegatedResourceAccountIndexCapsule indexCapsule = get(address);
    if (indexCapsule != null) {
      return indexCapsule;
    }
    return getWithPrefix(FROM_PREFIX, TO_PREFIX, address);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L162-182)
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
```
