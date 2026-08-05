### Title
Unbounded per-owner delegate index causes O(n log n) sort cost on every public GetDelegatedResourceAccountIndex(V2) read - ([File: chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java])

### Summary
`getWithPrefix` (invoked by both `getIndex` and `getV2Index`) fetches *all* rows matching a per-address key prefix via `prefixQuery` and sorts them with `Comparator.comparing(...)` without any bound, limit, or pagination on the result set size. Since an attacker can grow the number of persisted `(owner, receiver)` delegate entries under a single owner address without any protocol-level cap, every future public API call for that owner's delegated-resource index does full O(n log n) work, and this cost recurs on every read, forever, for every full/solidity/PBFT node serving that address.

### Finding Description
`getWithPrefix` builds `key = prefix + address`, retrieves every stored value under that prefix via `this.prefixQuery(key)`, converts it to an `ArrayList`, and sorts the whole list with `Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp)` twice (once for the "to" list, once for the "from" list) [1](#0-0) . This is reached from `getIndex`/`getV2Index`, which are called from `Wallet.getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2`, which are in turn wired to the public HTTP/gRPC endpoints `GetDelegatedResourceAccountIndex` and `GetDelegatedResourceAccountIndexV2` (also mirrored on the PBFT and Solidity HTTP interfaces) [2](#0-1) .

Each `DelegateResourceContract` transaction persists a new row keyed by `prefix + from + to` via `delegate`/`delegateV2` [3](#0-2) . Since the key includes the distinct receiver address, an attacker controlling one owner account can grow the number of entries under that owner's prefix arbitrarily by delegating to N distinct receiver accounts (`DelegateResourceActuator.validate/execute`), the only per-call constraints being: `delegateBalance >= 1 TRX`, receiver must exist and not be a contract, and receiver != owner [4](#0-3) . There is no cap anywhere in the actuator, the store, or the read path on the total number of distinct receivers/entries a single owner can accumulate.

Once created, these entries are permanent chain state (removed only by explicit `unDelegate`/`unDelegateV2`), so the cost to the attacker is a one-time (linear, bandwidth/energy/TRX-priced) cost to create N entries, while the cost imposed on every node answering `GetDelegatedResourceAccountIndex(V2)` for that address is recurring O(n log n) per call, indefinitely, with no pagination or truncation applied at the store or servlet layer.

### Impact Explanation
This is a public read-amplification / resource-exhaustion concern rather than a fund-theft or consensus-divergence bug: repeated calls to a public, unauthenticated read API against an address with a large N can consume disproportionate CPU on serving nodes (full node, PBFT node, solidity node) relative to the request's rate-limiting cost, potentially degrading availability of that API for other users. It does not by itself cause double-spend, replay, or state divergence between nodes, since all nodes compute the same deterministic (though expensive) sort.

### Likelihood Explanation
Exploitability requires the attacker to actually pay for and execute N real `DelegateResourceContract` transactions (each requiring a distinct, existing, non-contract receiver account and ≥1 TRX delegated balance), and N account-creation costs if distinct receivers must be created — this is a real, recurring on-chain economic cost, gated by `RateLimiterServlet` for request volume but not by any cap on N itself [5](#0-4) . Because the entries persist indefinitely and there is no server-side cap on N before the sort/collect pipeline runs, the attack is repeatable and the per-read cost is a permanent liability once N is grown, even though the initial cost to the attacker scales linearly with N (not free).

### Recommendation
Enforce a maximum number of delegate index entries per owner address at write time (reject/require pruning in `DelegateResourceActuator.validate`), and/or add pagination/limit parameters to `getWithPrefix`/`getIndex`/`getV2Index` and the corresponding gRPC/HTTP API so a single read cannot force sorting/serialization of an unbounded result set.

### Proof of Concept
Java integration test plan (in `framework/src/test/java/org/tron/core/db/DelegatedResourceAccountIndexStoreTest.java` style):
1. For increasing N ∈ {100, 1,000, 10,000, 100,000}, call `delegatedResourceAccountIndexStore.delegateV2(ownerAddress, receiver_i, timestamp_i)` for N distinct receiver addresses.
2. Measure wall-clock latency of `delegatedResourceAccountIndexStore.getV2Index(ownerAddress)` (equivalently invoke `wallet.getDelegatedResourceAccountIndexV2`) for each N.
3. Assert that latency grows super-linearly with N (consistent with O(n log n)) and that there is no exception/rejection/truncation for any N, demonstrating the absence of a server-enforced maximum before the sort/collect pipeline in `getWithPrefix` executes [1](#0-0) .

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L118-137)
```java
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
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L19-19)
```java
public class GetDelegatedResourceAccountIndexV2Servlet extends RateLimiterServlet {
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L60-69)
```java
  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndexV2(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-209)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }

    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
        processor.updateUsageForDelegated(ownerCapsule);

        long accountNetUsage = ownerCapsule.getNetUsage();
        if (null != this.getTx() && this.getTx().isTransactionCreate()) {
          accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
                  ownerCapsule.getFrozenV2BalanceForBandwidth());
        }
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
      }
      break;
      case ENERGY: {
        EnergyProcessor processor = new EnergyProcessor(dynamicStore, accountStore);
        processor.updateUsage(ownerCapsule);

        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (dynamicStore.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));
        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
    }

    byte[] receiverAddress = delegateResourceContract.getReceiverAddress().toByteArray();

    if (!DecodeUtil.addressValid(receiverAddress)) {
      throw new ContractValidateException("Invalid receiverAddress");
    }


    if (Arrays.equals(receiverAddress, ownerAddress)) {
      throw new ContractValidateException(
          "receiverAddress must not be the same as ownerAddress");
    }

    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (receiverCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + NOT_EXIST_STR);
    }
```
