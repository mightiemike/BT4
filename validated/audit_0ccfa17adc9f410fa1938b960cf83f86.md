### Title
Denial-of-Service via unbounded, O(n) list scan/removal in the legacy Delegated Resource Index path - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java], [File: actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java])

### Summary
The reported bug class — an unbounded array of IDs that is linearly scanned/mutated in a storage-backed structure during an unprivileged user action, causing rising gas cost and potential DoS — has a direct analog in java-tron's legacy (pre-`AllowDelegateOptimization`) delegated-resource bookkeeping. `FreezeBalanceActuator.delegateResource` and `UnfreezeBalanceActuator.execute` maintain `toAccountsList`/`fromAccountsList` inside `DelegatedResourceAccountIndexCapsule` using `List.contains()` and `ArrayList.remove()`, both O(n) in the number of delegation counterparties, and re-serialize/re-store the entire list on every call.

### Finding Description
When `DynamicPropertiesStore.supportAllowDelegateOptimization()` is false (which is the on-chain default value, `allowDelegateOptimization = 0`, until a committee proposal `#69` activates it — see [1](#0-0)  and [2](#0-1) ), every `FreezeBalanceContract` with a receiver goes through the legacy branch of `delegateResource`: [3](#0-2) 

Here `ownerIndexCapsule.getToAccountsList()` is fetched from storage as a full `List<ByteString>`, checked with `contains()` (O(n)), and — if a duplicate isn't found — a new element is appended via `addToAccount`, which rebuilds the whole protobuf list and re-persists it via `delegatedResourceAccountIndexStore.put(...)`. The mirror image happens for `fromAccountsList` on the receiver side.

When resources are unfrozen, `UnfreezeBalanceActuator.execute` reverses this by copying the entire list into a new `ArrayList`, calling `.remove(ByteString)` (O(n) linear scan) and writing the full list back to the store: [4](#0-3) 

There is no upper bound enforced on how many distinct receiver/owner addresses can accumulate in `toAccountsList`/`fromAccountsList` — an account can freeze-and-delegate to an unlimited number of distinct receiver addresses (each addition simply appends, as demonstrated by the test that grows the list to over 100 entries, [5](#0-4) ). Every subsequent delegate/undelegate action against that account then re-reads, re-scans, and re-writes the entire (unboundedly growing) list, exactly mirroring the `_unstake` for-loop-over-storage-array pattern in the external report: cost grows linearly with the number of past counterparties, and the write itself (`put`) touches the full serialized list on every call.

### Impact Explanation
An attacker (or any ordinary account, since this requires no privilege) can cheaply grow `toAccountsList`/`fromAccountsList` for a victim address by repeatedly delegating tiny amounts of frozen balance to many distinct throwaway receiver addresses. Each of these delegate calls is a normal `FreezeBalanceContract` invocation available to any unprivileged user. Once the list is large enough:
- Every subsequent `FreezeBalanceActuator`/`UnfreezeBalanceActuator` execution against the victim account incurs unbounded linear-time list scans and rewrites of the full serialized index, increasing per-transaction resource/bandwidth consumption disproportionately to the "useful" work performed.
- In the worst case this can push a legitimate unfreeze/undelegate transaction for the affected account toward CPU/time or block resource limits, denying or substantially degrading the account owner's ability to reclaim frozen TRX in a timely, cost-effective manner — the same "investor denied access to staked funds" impact described in the source report.

### Likelihood Explanation
The vulnerable branch is gated by `!dynamicPropertiesStore.supportAllowDelegateOptimization()`, and `allowDelegateOptimization` defaults to `0` (disabled) both in `reference.conf` and in the on-chain dynamic property fallback ( [6](#0-5) ), only becoming `1` once committee proposal `#69 ALLOW_DELEGATE_OPTIMIZATION` is passed on a given chain ( [7](#0-6) ). Any network (private chain, testnet, or a mainnet-like chain instantiated from this codebase) that has not yet passed this proposal exposes the vulnerable O(n) code path by default, and triggering it costs only the ordinary freeze/delegate transaction fee, making the likelihood of exploitation high for any such deployment.

### Recommendation
- Bound the number of distinct delegation counterparties per account (reject new delegations once a configurable cap is reached), or
- Always use the O(1) key-based `delegate`/`unDelegate` (the `AllowDelegateOptimization` `V2` scheme already present in `DelegatedResourceAccountIndexStore`) instead of gating it behind an on-chain proposal, so the unbounded-list code path is never reachable, or
- If backward compatibility must be preserved, force `convert()`/migration of the legacy index to the V2 scheme automatically instead of relying on the proposal being separately voted in by the committee.

### Proof of Concept
1. Deploy/run a java-tron network where `allowDelegateOptimization` has not been set to `1` (default state).
2. From account `A`, repeatedly submit `FreezeBalanceContract` transactions with `resource_type=BANDWIDTH` and a unique `receiver_address` each time (e.g., thousands of freshly generated addresses), each delegating a minimal frozen balance.
3. Observe that `DelegatedResourceAccountIndexCapsule` for `A` (`toAccountsList`) grows unboundedly, matching the pattern demonstrated in [5](#0-4) .
4. Submit an `UnfreezeBalanceContract` for one of the delegations; observe that `UnfreezeBalanceActuator.execute` copies and linearly scans/removes from the full grown list ( [4](#0-3) ), with per-call cost scaling with the number of prior delegations, degrading as the list grows.

### Citations

**File:** common/src/main/resources/reference.conf (L863-863)
```text
  allowDelegateOptimization = 0       # getAllowDelegateOptimization, #69: enable delegate optimization
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L950-955)
```java
    try {
      this.getAllowDelegateOptimization();
    } catch (IllegalArgumentException e) {
      this.saveAllowDelegateOptimization(
          CommonParameter.getInstance().getAllowDelegateOptimization());
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2739-2741)
```java
  public boolean supportAllowDelegateOptimization() {
    return getAllowDelegateOptimization() == 1L;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L320-345)
```java
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

**File:** framework/src/test/java/org/tron/core/actuator/FreezeBalanceActuatorTest.java (L282-296)
```java
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

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L598-607)
```java
      case ALLOW_DELEGATE_OPTIMIZATION: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_6)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_DELEGATE_OPTIMIZATION]");
        }
        if (value != 1) {
          throw new ContractValidateException(
              "This value[ALLOW_DELEGATE_OPTIMIZATION] is only allowed to be 1");
        }
        break;
```
