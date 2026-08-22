### Title
Unbounded per-address key growth in `DelegatedResourceAccountIndexStore` V2 index enables RPC-API DoS via `GetDelegatedResourceAccountIndexV2` prefix scan - (File: chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java)

### Summary
An anonymous actor can call `DelegateResourceContract` (Stake 2.0 "delegate resource") from an unlimited number of freshly-generated, cheaply-funded owner addresses, each delegating the 1-TRX minimum to the same victim `receiverAddress`. Each such call writes a new, permanent key into `DelegatedResourceAccountIndexStore` under a prefix keyed by the victim's address. Any subsequent query for that victim's delegation index (`getV2Index`) must scan and sort the entire, attacker-inflatable key range, which is the same unbounded "attacker grows victim's list" primitive described in the analog report.

### Finding Description
`DelegateResourceActuator.delegateResource()` writes two keys per delegation, one of them keyed by the **receiver** (victim) address concatenated with the caller's own address: [1](#0-0) 

The underlying store implementation appends entries to a virtually unbounded key range for that receiver: [2](#0-1) 

Reading this index back (`getV2Index`) performs a full LevelDB/RocksDB prefix scan across *all* entries for the target address and then sorts them by timestamp: [3](#0-2) 

This `getV2Index` call is exposed unauthenticated over gRPC and HTTP: [4](#0-3) 

The only cost gate on creating a new entry is the `DelegateResourceContract` validation, which merely requires the delegated balance to be `>= 1 TRX` and that the owner account has that much frozen (Stake 2.0) balance available: [5](#0-4) 

Since the "owner" side of the relation is the caller's own address, an attacker can generate an unlimited number of distinct owner accounts (each funded with only a small, single-use amount of TRX to freeze and delegate the 1-TRX minimum), and issue one `FreezeBalanceV2Contract` + `DelegateResourceContract` transaction pair per account, all delegating to the same victim address. Each pair creates one more permanent `V2_TO_PREFIX + receiverAddress + ownerAddress` entry that the victim can never remove (only the delegating owner can call `UnDelegateResourceContract` to remove their own entry; the victim has no way to force removal of relations others created against them).

This mirrors the reported bug class: an unprivileged actor pushes state into a data structure keyed by a victim address, and a store-iteration routine over that structure grows unboundedly with attacker-controlled input.

### Impact Explanation
Unlike the original `dMute` finding, no core state-transition function in java-tron (freeze, unfreeze, undelegate, resource consumption) iterates this index — those actuators use direct, O(1) keyed lookups (`DelegatedResourceCapsule.createDbKeyV2`), so asset redemption/accounting itself is not blocked. The impact is confined to the read-only index query path (`GetDelegatedResourceAccountIndexV2` over gRPC/HTTP and the equivalent PBFT/Solidity read services), where a full node performing the prefix scan and sort over an attacker-inflated key range can suffer excessive CPU/I/O and produce oversized responses, degrading or denying that API for the targeted address. This is a DoS-via-RPC-API condition, but it does not cause fund loss, accounting corruption, or consensus divergence.

### Likelihood Explanation
Moderate-to-low. The attack is unauthenticated and requires no privileged role, but it is not free: each additional poisoning entry requires a distinct on-chain account funded with at least 1 TRX (to satisfy `FreezeBalanceV2Contract`/`DelegateResourceContract` minimums) plus transaction fees, unlike the original `dMute` PoC where thousands of entries cost negligible wei. This materially raises the cost of inflating a victim's index to a size that meaningfully degrades query performance, and the impact is limited to a non-critical read API rather than a fund-blocking function.

### Recommendation
- Cap the number of distinct delegation relations indexed per address, or move to O(1) existence checks instead of unbounded per-address key enumeration for the V2 index.
- Add pagination/limits to `getV2Index`/`getWithPrefix` so a single query cannot force a full unbounded prefix scan, and enforce response-size limits in `GetDelegatedResourceAccountIndexV2Servlet` and the corresponding gRPC handler.
- Consider rate-limiting or increasing the economic cost of creating many small delegation relations targeting the same receiver address.

### Proof of Concept
Conceptual PoC (cannot be executed without live node access):
1. Generate N fresh keypairs; fund each with the minimum TRX needed to freeze 1 TRX under Stake 2.0 (`FreezeBalanceV2Contract`).
2. From each keypair, freeze 1 TRX for BANDWIDTH, then submit `DelegateResourceContract` with `receiverAddress = victim`, `balance = 1_000_000` (1 TRX).
3. Repeat for large N (e.g., tens of thousands), each creating a new `V2_TO_PREFIX+victim+ownerAddress` key per `DelegatedResourceAccountIndexStore.delegateV2` at [2](#0-1) .
4. Query `GetDelegatedResourceAccountIndexV2` for `victim` and observe growing latency/response size as the prefix scan in `getWithPrefix` at [6](#0-5)  processes the inflated key range.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-150)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L313-316)
```java
    //modify DelegatedResourceAccountIndexStore
    delegatedResourceAccountIndexStore.delegateV2(ownerAddress, receiverAddress,
        dynamicPropertiesStore.getLatestBlockHeaderTimestamp());

```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L77-89)
```java
  public void delegateV2(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(V2_FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(V2_TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L114-138)
```java
  public DelegatedResourceAccountIndexCapsule getV2Index(byte[] address) {
    return getWithPrefix(V2_FROM_PREFIX, V2_TO_PREFIX, address);
  }

  private DelegatedResourceAccountIndexCapsule getWithPrefix(byte[] fromPrefix, byte[] toPrefix, byte[] address) {
    DelegatedResourceAccountIndexCapsule tmpIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(address));

    byte[] key = Bytes.concat(fromPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpToList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpToList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    List<ByteString> list = tmpToList.stream()
        .map(DelegatedResourceAccountIndexCapsule::getAccount).collect(Collectors.toList());
    tmpIndexCapsule.setAllToAccounts(list);

    key = Bytes.concat(toPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpFromList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpFromList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    list = tmpFromList.stream().map(DelegatedResourceAccountIndexCapsule::getAccount).collect(
        Collectors.toList());
    tmpIndexCapsule.setAllFromAccounts(list);
    return tmpIndexCapsule;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L1-2)
```java
package org.tron.core.services.http;

```
