### Title
Unbounded per-account asset-map iteration in bandwidth accounting can be inflated by anonymous token transfers, degrading transaction/block processing - (File: chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java)

### Summary
`BandwidthProcessor.updateUsage(AccountCapsule accountCapsule)` iterates over the *entire* TRC10 asset map of an account (`getAssetMap()` and `getAssetMapV2()`, merged with `getAllFreeAssetNetUsageV2()`) every time bandwidth usage is updated for that account, i.e. on essentially every transaction the account is involved in. [1](#0-0)  The size of this map is attacker-controllable: any address can receive an arbitrary number of distinct TRC10 asset deposits via `TransferAssetContract`, which is analogous to the reported Cosmos SDK bug where `GetAllBalances` iterates an attacker-inflatable balance set at genesis.

### Finding Description
`updateUsage` merges `accountCapsule.getAssetMapV2()` with `accountCapsule.getAllFreeAssetNetUsageV2()` and then runs a `forEach` that performs bandwidth-recovery math (`increase(...)`) for every entry: [2](#0-1) . When `allowSameTokenName == 0` it additionally iterates the legacy `getAssetMap()`: [3](#0-2) .

`getAssetMapV2()` calls `importAllAsset()`, which — when asset-balance optimization is enabled — performs a full `prefixQuery` over the account's address prefix in `AccountAssetStore` to enumerate *every* asset id ever deposited to that address: [4](#0-3)  and [5](#0-4) .

Because TRC10 asset transfers are a normal, unprivileged, low-cost transaction type available to any account, an attacker can send tiny amounts of many distinct pre-existing TRC10 tokens (tens/hundreds of thousands of TRC10 assets already exist on-chain) to a victim address. Every such deposit adds a persistent entry to that address's asset map/prefix range. There is no bound or fee scaling with the number of distinct asset types held by an address — the cost to the attacker is proportional to the number of cheap transfer transactions sent, while the cost imposed on every future transaction processed for the victim address (and on every node/witness that must re-validate blocks containing such transactions) is proportional to the number of distinct assets accumulated.

This is the same bug class as the reported issue: an unbounded, attacker-inflatable iteration over a balance/asset collection tied to an address that anyone can deposit into, executed unconditionally in a core accounting path (`consumeBandwidth` → `updateUsage`), rather than being scoped/bounded to only the assets relevant to the current transaction.

### Impact Explanation
`updateUsage` runs inside `BandwidthProcessor.consume`, which is invoked during transaction execution for every transaction touching the affected account, i.e. on the consensus-critical block-processing path executed by every full node/witness. If an attacker inflates a target address (e.g., an exchange hot wallet, a contract-owned address, or any frequently-used account) with a very large number of distinct TRC10 asset entries, every subsequent transaction sent by or to that account will incur O(n) work in this loop during block application. At sufficient scale this can materially slow down block processing/validation for that account's transactions, increasing the risk of block-processing timeouts and degraded throughput — a denial-of-service on transaction/block processing rather than an isolated genesis-time issue.

### Likelihood Explanation
Likelihood is limited by the fact that acquiring a large number of distinct TRC10 asset holdings requires many separate cheap transfer transactions (bandwidth/fee cost per transfer) sent to the target address; there is no single low-cost action that inflates the map instantly. It is unclear from the indexed code whether current mainnet parameters (asset-optimization flag, allowSameTokenName) make this practically exploitable at meaningful scale, and this could not be fully verified without deeper review of `DynamicPropertiesStore` defaults and TRC10 transfer fee/bandwidth costs at scale.

### Recommendation
Avoid iterating the full asset/asset-usage map when it is not needed for the transaction being processed. Recovery of free-asset-net usage should be computed lazily, only for the specific asset id(s) referenced by the transaction (as is already done in `useAssetAccountNet`), rather than eagerly recomputing recovery for every asset an account has ever held. If a full recomputation is required for API/query purposes, consider bounding/paginating the iteration and/or bounding the number of distinct TRC10 asset types an address is allowed to accumulate.

### Proof of Concept
Not independently verified in a live environment (index access only); root cause is demonstrated statically:
1. `TransferAssetContract` allows any account to send an arbitrary existing TRC10 asset to any destination address without restriction on the number of distinct asset types the destination can accumulate.
2. Repeatedly transferring small amounts of many distinct existing TRC10 tokens to one victim address grows that address's `AssetV2Map`/`AccountAssetStore` prefix range unboundedly.
3. Any subsequent transaction referencing that address triggers `BandwidthProcessor.consume` → `updateUsage(accountCapsule)`, which performs `getAssetMapV2()` (full prefix scan when asset-optimization is enabled) and a `forEach` recovery computation over all entries: [2](#0-1) , causing processing cost to scale with the attacker-controlled asset count.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L47-79)
```java
  public void updateUsage(AccountCapsule accountCapsule) {
    long now = chainBaseManager.getHeadSlot();
    long oldNetUsage = accountCapsule.getNetUsage();
    long latestConsumeTime = accountCapsule.getLatestConsumeTime();
    accountCapsule.setNetUsage(increase(accountCapsule, BANDWIDTH,
            oldNetUsage, 0, latestConsumeTime, now));
    long oldFreeNetUsage = accountCapsule.getFreeNetUsage();
    long latestConsumeFreeTime = accountCapsule.getLatestConsumeFreeTime();
    accountCapsule.setFreeNetUsage(increase(oldFreeNetUsage, 0, latestConsumeFreeTime, now));

    if (chainBaseManager.getDynamicPropertiesStore().getAllowSameTokenName() == 0) {
      Map<String, Long> assetMap = accountCapsule.getAssetMap();
      assetMap.forEach((assetName, balance) -> {
        long oldFreeAssetNetUsage = accountCapsule.getFreeAssetNetUsage(assetName);
        long latestAssetOperationTime = accountCapsule.getLatestAssetOperationTime(assetName);
        accountCapsule.putFreeAssetNetUsage(assetName,
            increase(oldFreeAssetNetUsage, 0, latestAssetOperationTime, now));
      });
    }
    Map<String, Long> assetMapV2 = accountCapsule.getAssetMapV2();
    Map<String, Long> map = new HashMap<>(assetMapV2);
    accountCapsule.getAllFreeAssetNetUsageV2().forEach((k, v) -> {
      if (!map.containsKey(k)) {
        map.put(k, 0L);
      }
    });
    map.forEach((assetName, balance) -> {
      long oldFreeAssetNetUsage = accountCapsule.getFreeAssetNetUsageV2(assetName);
      long latestAssetOperationTime = accountCapsule.getLatestAssetOperationTimeV2(assetName);
      accountCapsule.putFreeAssetNetUsageV2(assetName,
          increase(oldFreeAssetNetUsage, 0, latestAssetOperationTime, now));
    });
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java (L97-109)
```java
  public Map<String, Long> getAllAssets(Protocol.Account account) {
    Map<String, Long> assets = new HashMap<>();
    if (account.getAssetOptimized()) {
      Map<WrappedByteArray, byte[]> map = prefixQuery(account.getAddress().toByteArray());
      map.forEach((k, v) -> {
        byte[] assetID = ByteArray.subArray(k.getBytes(),
                account.getAddress().toByteArray().length, k.getBytes().length);
        assets.put(ByteArray.toStr(assetID), Longs.fromByteArray(v));
      });
    }
    account.getAssetV2Map().forEach((k, v) -> assets.put(k, v));
    return assets;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java (L46-52)
```java
  public static Account importAllAsset(Account account) {
    if (!isAllowAssetOptimization()) {
      return account;
    }
    Map<String, Long> map = accountAssetStore.getAllAssets(account);
    return account.toBuilder().clearAssetV2().putAllAssetV2(map).build();
  }
```
