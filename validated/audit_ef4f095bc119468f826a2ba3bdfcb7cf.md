## Title
Unbounded growth of `DelegatedResourceAccountIndexCapsule.toAccountsList`/`fromAccountsList` enables DoS via cheap resource-delegation spam - (`actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`)

## Summary
`FreezeBalanceActuator.delegateResource()` (legacy resource-delegation path, still reachable when `supportAllowDelegateOptimization()` is false) appends the counter-party address to a protobuf-backed list (`toAccountsList` / `fromAccountsList`) stored inside a single `DelegatedResourceAccountIndexCapsule` record, with no upper bound on list size, mirroring the SHToken `users` array DoS pattern from the external report.

## Finding Description
When a `FreezeBalanceContract` delegates resources to a distinct receiver, and the chain has not enabled `AllowDelegateOptimization`, the actuator loads (or creates) the owner's `DelegatedResourceAccountIndexCapsule`, checks membership with a linear `List.contains()` scan, and appends via `addToAccount()`/`addFromAccount()` — with no cap on the number of distinct entries: [1](#0-0) 

The underlying capsule stores this as a single repeated protobuf field with no size limit, and removal likewise does a linear scan/copy: [2](#0-1) 

Because the whole `toAccountsList`/`fromAccountsList` is round-tripped (deserialize entire record → linear `contains()` scan → append → reserialize → `put()`) on every delegate/undelegate operation touching that account, the cost of each subsequent call grows linearly with the number of distinct counter-parties already recorded, giving O(n²) total cost for an attacker who delegates a minimal amount to n unique addresses — directly analogous to the reported `users` array/`deleteUserFromArray()` pattern. The existing test confirms unbounded accumulation is the expected, uncapped behavior (100+ receivers recorded without limit): [3](#0-2) 

`UnfreezeBalanceActuator` performs the mirrored linear remove operation on unfreeze: [4](#0-3) 

I was unable to fully confirm, within the remaining investigation budget, (a) the default on-chain value of `AllowDelegateOptimization` (i.e., whether the vulnerable legacy code path is active by default on a freshly-configured java-tron network, or only reachable on chains where a committee proposal has not yet enabled the optimization), and (b) whether there is any hard cap elsewhere (e.g., in `FreezeBalanceActuator.validate()`) on the number of distinct receiver addresses a single account can delegate to. My last `grep_search` for `AllowDelegateOptimization`/`ALLOW_DELEGATE_OPTIMIZATION` in `DynamicPropertiesStore.java` returned no matches even though an earlier search showed 4 matches in that same file, and my `read_file` call for `FreezeBalanceActuator.java` failed due to a missing parameter — these could not be re-run because tool access ended. This means I cannot state with certainty whether this legacy path is live on current mainnet, only that the code implementing it (and its unbounded-array-append design) is present in this repository snapshot.

## Impact Explanation
If the legacy (non-optimized) delegated-resource-index path is reachable on a live network (i.e., `AllowDelegateOptimization` not yet enabled by committee proposal, or on any private/testnet deployment using default settings), an attacker can cheaply freeze a minimal balance and delegate to thousands of unique addresses. Because the per-account index record grows without bound and is read/written wholesale on every delegate/undelegate transaction touching that account, this could degrade block-processing performance for that account's freeze/delegate/unfreeze transactions and inflate state-DB record size, similarly to the reported SHToken DoS class (transaction processing failures/slowdowns due to unbounded array operations), though it does not directly cause consensus divergence or fund loss.

## Likelihood Explanation
Likelihood depends entirely on whether `supportAllowDelegateOptimization()` returns false for the target network. If the optimization has been enabled via committee proposal (which replaces this data structure with a prefix-keyed store, avoiding the unbounded single-blob list — see `DelegatedResourceAccountIndexStore.delegate()`), the vulnerable code path is not reachable and likelihood is low: [5](#0-4) 
If the optimization is not yet enabled on a given network, any account holder can trigger the growth cheaply and repeatedly via ordinary `FreezeBalanceContract` (delegated) transactions, making likelihood moderate-to-high on such networks.

## Recommendation
- Confirm and, if necessary, force-enable `AllowDelegateOptimization` by default on all networks, or otherwise migrate/deprecate the legacy list-based `DelegatedResourceAccountIndexCapsule.toAccountsList`/`fromAccountsList` code path entirely.
- Add an explicit cap on the number of distinct delegation counter-parties tracked per account in the legacy path (mirroring caps already present elsewhere, e.g. `UNFREEZE_MAX_TIMES` for `unfrozenV2`), rejecting further delegation to new unique addresses once the cap is reached.
- Avoid O(n) `contains()`/copy-based membership checks on every call; if the legacy structure must remain supported for backward compatibility, bound its growth and/or migrate accounts to the prefix-indexed store proactively rather than lazily via `convert()`.

## Proof of Concept
Not independently reproduced in this session (no execution environment). The existing repository test `testMultiFreezeDelegatedBalanceForBandwidth` (`framework/src/test/java/org/tron/core/actuator/FreezeBalanceActuatorTest.java:275-337`) already demonstrates the mechanism: it seeds `RECEIVE_COUNT` (100) distinct addresses into `ownerIndexCapsule.addToAccount(...)` and confirms the list grows to `RECEIVE_COUNT + 1` entries with no cap enforced, which is the same operation an attacker could drive to a much larger, unbounded count via repeated `FreezeBalanceContract` (delegated) transactions on a network where `AllowDelegateOptimization` is disabled.

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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L63-75)
```java
  public void delegate(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```
