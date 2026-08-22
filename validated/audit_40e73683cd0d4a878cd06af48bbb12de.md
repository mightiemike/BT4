## Title
Unbounded legacy DelegatedResourceAccountIndex list can be inflated by any user and forcibly iterated inside an unrelated victim-referencing transaction (DoS analog to the Panoptic `positionIdList` issue) - (File: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`)

### Summary
The reported bug class is: an unbounded per-account list, cheaply grown by an attacker, that later gets fully iterated during a critical state-transition and can exceed resource limits, blocking that operation for the account (or for whoever triggers the iteration). In java-tron, `DelegatedResourceAccountIndexStore`/`DelegatedResourceAccountIndexCapsule` maintains a legacy (`FROM_PREFIX`/`TO_PREFIX`) per-address list of delegation counterparties that any sender can append to for free, and the migration routine `convert()` walks the *entire* legacy list for a given address in one call. This conversion is triggered inside `FreezeBalanceActuator`, `DelegateResourceActuator`, and `UnDelegateResourceActuator` whenever `supportAllowDelegateOptimization()` is active, and critically it is invoked on **both** the caller's own address and the arbitrary counterparty address supplied in the transaction — so an attacker can force a victim's dormant, attacker-inflated list to be iterated inside a transaction the victim never initiated.

### Finding Description
`DelegatedResourceAccountIndexCapsule` exposes `getToAccountsList()`/`getFromAccountsList()` backed by unbounded protobuf repeated fields [1](#0-0) .

In the legacy (pre-optimization) path of `FreezeBalanceActuator.delegateResource`, any account can append itself to another address's index list at essentially zero marginal cost (the only gate is `calcFee() == 0` for the actuator, and a minimal frozen amount) — the check is merely "not already present," and the list is unbounded: [2](#0-1) 

Once `supportAllowDelegateOptimization()` is enabled, the same method instead calls `convert(ownerAddress)` and `convert(receiverAddress)` before recording the new-format entry: [3](#0-2) 

`convert()` reads the full legacy `toAccountsList`/`fromAccountsList` for the given address and re-delegates every single entry via `this.delegate(...)` (which itself performs two DB `put`s per entry), then deletes the legacy record: [4](#0-3) 

Because `convert()` is called for the *receiver* address supplied by the caller (not just the caller's own address), an attacker can:
1. Before/while the legacy path is reachable, cheaply grow a target address's legacy `fromAccountsList` to an arbitrary size using many low-cost sender accounts (each `FreezeBalanceContract` delegation with a minimal 1 TRX freeze is free of protocol fee, `calcFee()` returns 0) [5](#0-4) .
2. Later submit (or wait for anyone to submit) any `FreezeBalanceContract`/`DelegateResourceContract`/`UnDelegateResourceContract` transaction naming that target as `receiverAddress`/`ownerAddress`, which forces a full, uncapped `convert()` walk of the victim's inflated list inside that transaction's execution — a computation cost the transaction's author did not intend to pay for and cannot bound in advance, and one the victim never consented to.

This mirrors the Panoptic `positionIdList` pattern: an unbounded, cheaply-grown, per-account list that is force-iterated during a later critical operation, potentially exceeding available energy/computation and disrupting normal account operation (freeze/delegate/undelegate) for the targeted address.

### Impact Explanation
If an attacker inflates a popular or high-traffic address's (e.g., an exchange hot wallet's) legacy delegation index to a very large size, any subsequent unrelated user transaction that references that address as a delegate counterparty will incur an unbounded, attacker-controlled iteration/write cost during `convert()`. This can cause that transaction to run out of energy/fail unexpectedly, and in aggregate increases block-processing cost for the network, since the cost is paid inside normal actuator execution during block application (not just validation). This is a resource-exhaustion/DoS vector reachable via ordinary broadcast transactions against the accounting/resource-delegation subsystem, not requiring any privileged actor.

### Likelihood Explanation
Growing the legacy list costs the attacker only the minimal per-delegation freeze amount (as low as 1 TRX, `calcFee()==0`), and no consent or special permission from the target address is required to be added to its legacy `fromAccountsList`. The conversion trigger only requires any future transaction (from any user, including the attacker themselves) referencing the inflated address in a `FreezeBalanceContract` (legacy path) or `DelegateResourceContract`/`UnDelegateResourceContract` call. The primary constraint is the continued existence of unconverted legacy-format entries for the targeted address (i.e., it has not yet had a delegate/undelegate/freeze transaction executed under `supportAllowDelegateOptimization()` since the attacker's inflation) — which is entirely plausible for infrequently-active but high-value addresses.

### Recommendation
Cap the number of legacy `toAccounts`/`fromAccounts` entries that can be appended per address (mirroring the pattern already used for `FrozenSupply` lists, `MarketSellAsset` order counts, and `UNFREEZE_MAX_TIMES`), and/or process `convert()` incrementally (batched across multiple blocks/transactions) rather than migrating an entire legacy list synchronously inside a single unrelated user transaction. Additionally, consider disallowing `convert()` from being triggered against an address purely because it was named as a counterparty by someone else's transaction, requiring the address owner's own transaction to trigger its own migration.

### Proof of Concept
1. While the legacy delegation index path is reachable (or for any address whose legacy entries have not yet been migrated), have N distinct low-balance accounts each submit a `FreezeBalanceContract` with `receiverAddress = victim`, each with `frozenBalance = 1 TRX` — each call appends one entry to `victim`'s legacy `fromAccountsList` at near-zero cost via `FreezeBalanceActuator.delegateResource` [6](#0-5) .
2. Once `victim`'s legacy list has grown to a large size (e.g., tens of thousands of entries), submit any transaction (`FreezeBalanceContract`, `DelegateResourceContract`, or `UnDelegateResourceContract`) referencing `victim` as receiver/owner while `supportAllowDelegateOptimization()` is enabled.
3. Observe that `DelegatedResourceAccountIndexStore.convert(victim)` performs O(N) `delegate()` calls (2 DB writes each) synchronously inside that transaction's execution [4](#0-3) , consuming disproportionate resources unrelated to the transaction's stated purpose and potentially causing failures for legitimate subsequent operations touching `victim`.

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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L284-287)
```java
  @Override
  public long calcFee() {
    return 0;
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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L347-353)
```java
    } else {
      // modify DelegatedResourceAccountIndexStore new
      delegatedResourceAccountIndexStore.convert(ownerAddress);
      delegatedResourceAccountIndexStore.convert(receiverAddress);
      delegatedResourceAccountIndexStore.delegate(ownerAddress, receiverAddress,
          dynamicPropertiesStore.getLatestBlockHeaderTimestamp());
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
