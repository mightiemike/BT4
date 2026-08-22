### Title
Unbounded growth of `DelegatedResourceAccountIndexCapsule` to/from-account lists enables gas/CPU DoS on freeze/unfreeze and RPC responses - (File: chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java, actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java, actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java)

### Summary
When the chain parameter `AllowDelegateOptimization` has not been activated (its legacy code path remains compiled and reachable via `FreezeBalanceActuator`/`UnfreezeBalanceActuator`), any account can grow another account's `toAccountsList`/`fromAccountsList` inside `DelegatedResourceAccountIndexCapsule` without any upper bound, purely by broadcasting many cheap `FreezeBalanceContract` transactions that delegate resources to a target address from many distinct (attacker-controlled, cheaply created) owner addresses. Later, any single unfreeze/undelegate operation that touches this list must copy the entire array, perform a linear `remove()`, and rewrite the whole protobuf-backed list back to the store, an O(n) cost with no cap on n, mirroring the reported `_removeSessionKey()` unbounded-array DoS pattern.

### Finding Description
`FreezeBalanceActuator.delegateResource()` maintains an account-index entry for every unique owner/receiver pair when the legacy (non-optimized) delegation model is active: [1](#0-0) 

Each call from a new, distinct owner address that has never delegated to the target `receiverAddress` appends a new entry via `addToAccount`/`addFromAccount` in `DelegatedResourceAccountIndexCapsule`: [2](#0-1) 

There is no limit on how many distinct owner addresses can delegate to the same receiver, or how many distinct receivers a single owner can delegate to — an attacker only needs to create N throwaway accounts (cheap on TRON) and issue N minimal `FreezeBalanceContract` transactions targeting the same victim `receiverAddress`, growing `fromAccountsList` for that victim without bound.

When any of these delegations is later unwound, `UnfreezeBalanceActuator.execute()` must materialize the *entire* list into a new `ArrayList`, perform a linear scan/removal, and rebuild+store the whole protobuf list again: [3](#0-2) 

This is structurally identical to the reported bug class: an unprivileged actor can force an unbounded array to be attached to a target account, and the “disable/remove” code path (here, `UnfreezeBalanceActuator`) must always fully copy/scan/rewrite that array with O(n) cost and no per-operation cap. As the list size n grows arbitrarily, the cost of this operation-scales linearly against a fixed per-transaction CPU/energy budget (`MAX_CPU_TIME_OF_ONE_TX`, energy limits), which can push a legitimate unfreeze transaction for that key past resource limits, causing it to fail deterministically.

The same unbounded arrays are also exposed to the outside world unpaginated via RPC/HTTP, allowing a read-side amplification/DoS as well: [4](#0-3) [5](#0-4) 

`getDelegatedResourceAccountIndex`/`GetDelegatedResourceAccountIndexServlet` return the full `DelegatedResourceAccountIndex` (unbounded `toAccountsList`/`fromAccountsList`) for any queried address with no pagination or size limit, so a bloated index built as above can also be leveraged to produce oversized responses on every read call.

### Impact Explanation
An attacker can permanently degrade or break resource-unfreezing for a targeted victim address by inflating its `fromAccountsList`/`toAccountsList` beyond what fits in a transaction's CPU/energy budget, mirroring the original report's “malicious session cannot be removed” impact: the victim’s legitimate unfreeze/undelegate transaction that hits the bloated index can be made to consistently fail, freezing/locking the victim's TRX and preventing normal resource management. It also creates unpaginated read amplification via the `GetDelegatedResourceAccountIndex(V2)` RPC/HTTP endpoints, degrading node response times/bandwidth for any caller of that address.

### Likelihood Explanation
This path is only reachable while `AllowDelegateOptimization` is not enabled on the network (`!dynamicStore.supportAllowDelegateOptimization()`), since when the optimization is enabled the actuators switch to per-pair keyed store entries (`delegate`/`unDelegate` in `DelegatedResourceAccountIndexStore`) instead of a single growing list. I could not verify from the indexed code whether `AllowDelegateOptimization` is enabled by default on current mainnet (no default value could be located in `DynamicPropertiesStore.java` within index limits), so likelihood is network/configuration-dependent: on any deployment (private chain, testnet, or a mainnet snapshot) where this proposal has not been activated, the legacy list-growth code remains fully reachable from ordinary, unprivileged `FreezeBalanceContract`/`UnfreezeBalanceContract` transactions, requiring only the cost of creating cheap accounts and minimal freeze amounts.

### Recommendation
- Cap the number of entries a single account's `toAccountsList`/`fromAccountsList` can hold in the legacy delegation model, or force migration to the prefix-keyed `DelegatedResourceAccountIndexStore` model (`convert()`) regardless of the `AllowDelegateOptimization` flag.
- Avoid full-list copy/rewrite semantics in `UnfreezeBalanceActuator`/`FreezeBalanceActuator`; use indexed/keyed storage (as already implemented in the optimized path) universally.
- Add pagination/size limits to `getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2` and their RPC/HTTP servlets to bound response size regardless of underlying list size.

### Proof of Concept
1. Attacker creates N throwaway accounts and funds each with the minimum TRX required to freeze balance.
2. From each of the N accounts, broadcast a `FreezeBalanceContract` with `receiverAddress` set to the victim address, while `AllowDelegateOptimization` is inactive; each call executes `FreezeBalanceActuator.delegateResource()`, appending one entry to the victim’s `fromAccountsList` via `DelegatedResourceAccountIndexCapsule.addFromAccount` (no bound enforced).
3. Repeat until `fromAccountsList` on the victim account is large enough that copying/scanning/rewriting it (as done in `UnfreezeBalanceActuator.execute()`, lines 162-188) exceeds the per-transaction CPU/energy budget.
4. Any subsequent unfreeze/undelegate transaction touching this list for the victim account fails deterministically due to resource-limit exhaustion, and/or callers of `GetDelegatedResourceAccountIndex`/`GetDelegatedResourceAccountIndexV2` receive oversized unpaginated responses for the victim address.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java (L57-94)
```java
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1040-1051)
```java
  public DelegatedResourceAccountIndex getDelegatedResourceAccountIndex(ByteString address) {
    if (address == null || address.size() != DecodeUtil.ADDRESS_SIZE / 2) {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
    DelegatedResourceAccountIndexCapsule accountIndexCapsule =
        chainBaseManager.getDelegatedResourceAccountIndexStore().getIndex(address.toByteArray());
    if (accountIndexCapsule != null) {
      return accountIndexCapsule.getInstance();
    } else {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java (L58-67)
```java
  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndex(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```
