### Title
Unbounded RocksDB prefix-scan in `AccountAssetStore.getAllAssets`/`getDeletedAssets` allows attacker-inflated iteration cost per RPC call - (File: chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java)

### Summary
`AccountAssetStore.getAllAssets(Protocol.Account)` and `getDeletedAssets(byte[])` perform a `prefixQuery` over the `account-asset` column keyed by `address || assetId`, and this scan is invoked on every account load/save when `supportAllowAssetOptimization()`/`getAllowAccountAssetOptimizationFromRoot()` is enabled, including via `AssetUtil.importAllAsset` (used when building `Account` responses) and via `SnapshotRoot.processAccount` on every merge/flush of an account that touches state. An attacker who accumulates a large number of distinct assetId entries under one address can make each of these operations scan proportionally more RocksDB keys.

### Finding Description
`getAllAssets` calls `prefixQuery(account.getAddress().toByteArray())` [1](#0-0) , and `getDeletedAssets` similarly calls `prefixQuery(key)` [2](#0-1) . These are reached from `SnapshotRoot.processAccount`, which is invoked on every `merge`/flush touching accounts when `needOptAsset()` is true [3](#0-2) , and from `AssetUtil.importAllAsset`, which is used to reconstruct an `Account`'s full asset map (e.g., for RPC responses) [4](#0-3) .

However, the scan cost is bounded by the number of **distinct asset entries stored under that specific address**, i.e., entries the attacker's own account actually holds in the `account-asset` DB (keys are `address || assetId`). To inflate this, the attacker must cause thousands of *distinct* TRC10 assetIds to appear as balances under their own address. This requires either:
1. Issuing thousands of distinct TRC10 tokens themselves (each `AssetIssueContract` carries a substantial, configurable TRX fee — `getAssetIssueFee()` in `DynamicPropertiesStore`, typically 1024 TRX per issuance in mainnet defaults), or
2. Receiving transfers of thousands of pre-existing distinct token IDs from other holders (which requires those tokens to already exist on-chain — again bounded by system-wide asset-issuance economics, not just the attacker's own bandwidth/energy).

The premise in the question that this is "bounded only by the number of `AssetIssueContract` transactions the attacker can afford in bandwidth/energy" is inaccurate: `AssetIssueContract` is fee-metered by TRX (a fixed, non-trivial issuance fee), not just bandwidth/energy, and this fee is charged per new token globally — it is a real, non-negligible economic cost that scales with N. This significantly raises the cost of the attack compared to a "cheap" DoS.

That said, the underlying code pattern is real: there is no upper bound enforced on the number of distinct assetId entries an address may accumulate in `AccountAssetStore`, and `prefixQuery` iteration cost is O(number of held distinct assets) with no cap, so a well-funded attacker prepared to pay the cumulative issuance fees (or acquire many pre-existing tokens) could still inflate per-account scan cost and repeatedly trigger it via `getAccount`/`GetAccountNet`-style RPCs at comparatively low cost after the one-time setup.

### Impact Explanation
Concrete impact class: **DoS via RPC-API** — repeated `getAccount` (or similar wallet RPCs that call into `AssetUtil.importAllAsset`/`AccountAssetStore.getAllAssets`) against a crafted address would force the full node to perform a `prefixQuery` scan over all of that address's held asset keys, consuming CPU/I/O proportional to attacker-controlled N. This matches the "DoS via RPC-API" bounty class in scope, but the severity is limited: it does not crash consensus or corrupt state, and the cost to build up large N is non-trivial (see below), unlike a truly "free" attack vector.

### Likelihood Explanation
- Precondition `supportAllowAssetOptimization()==1` is required and assumed true per the prompt.
- To make N large (e.g., thousands), the attacker must pay the network's TRC10 issuance fee for each distinct token (a substantial TRX outlay per token, configurable via a proposal but non-zero by default), or otherwise acquire thousands of pre-existing distinct tokens by transfer — both of which impose real economic cost well beyond ordinary bandwidth/energy fees claimed in the prompt.
- Once the attacker holds N asset entries, re-triggering the scan via RPC is cheap and repeatable (read-only RPC calls typically have no on-chain fee), so the "repeat attack cheaply" part of the claim is valid, but the "create N holdings cheaply" part is not.
- Overall likelihood is moderate-to-low: feasible for attackers willing to spend on issuance fees, but not "cheap" as characterized in the question.

### Recommendation
- Cap the number of distinct asset entries considered when iterating (e.g., limit `prefixQuery` results per call, or paginate) in `AccountAssetStore.getAllAssets`/`getDeletedAssets`.
- Consider bounding the total number of distinct TRC10 assetIds a single account may hold, or charging bandwidth/resource cost proportional to the number of distinct assets returned when serving `getAccount`-style RPCs that call `importAllAsset`.
- Add a maximum asset-count guard in `AssetIssueActuator`/transfer paths, or throttle `getAllAssets` RPC calls with per-key iteration ceilings and short-circuit warnings/logging when a single address's asset key count crosses a threshold.

### Proof of Concept
Not fully reproducible as a "cheap" DoS given real chain economics. A minimal PoC to demonstrate the mechanism (not the "cheap" claim) would be:
```java
// Pseudocode / JUnit-style illustration
AccountCapsule attacker = new AccountCapsule(...);
for (int i = 0; i < N; i++) {
  attacker.addAssetAmountV2(("token" + i).getBytes(), 1L, dynamicPropertiesStore); // requires token"i" to actually exist on-chain
}
accountAssetStore.putAccount(attacker.getInstance());

long start = System.nanoTime();
Map<String, Long> assets = accountAssetStore.getAllAssets(attacker.getInstance());
long elapsed = System.nanoTime() - start;
// assertion: elapsed scales roughly linearly with N, demonstrating unbounded prefixQuery cost,
// but each of the N tokens requires a separate, fee-bearing AssetIssueContract to exist on-chain first.
```
This confirms the scan-cost mechanism described, but the PoC cannot show N growing "cheaply" via bandwidth/energy alone — creating N distinct TRC10 tokens requires N TRX-denominated issuance fees, which is the key economic check the original claim omits.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java (L72-77)
```java
  public Map<WrappedByteArray, WrappedByteArray> getDeletedAssets(byte[] key) {
    Map<WrappedByteArray, WrappedByteArray> assets = new HashMap<>();
    prefixQuery(key).forEach((k, v) ->
            assets.put(WrappedByteArray.of(k.getBytes()), WrappedByteArray.of(null)));
    return assets;
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

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotRoot.java (L124-148)
```java
  private void processAccount(Map<WrappedByteArray, WrappedByteArray> batch) {
    AccountAssetStore assetStore = ChainBaseManager.getInstance().getAccountAssetStore();
    Map<WrappedByteArray, WrappedByteArray> accounts = new HashMap<>();
    Map<WrappedByteArray, WrappedByteArray> assets = new HashMap<>();
    batch.forEach((k, v) -> {
      if (ByteArray.isEmpty(v.getBytes())) {
        accounts.put(k, v);
        assets.putAll(assetStore.getDeletedAssets(k.getBytes()));
      } else {
        AccountCapsule item = new AccountCapsule(v.getBytes());
        if (!item.getAssetOptimized()) {
          assets.putAll(assetStore.getDeletedAssets(k.getBytes()));
          item.setAssetOptimized(true);
        }
        assets.putAll(assetStore.getAssets(item.getInstance()));
        item.clearAsset();
        accounts.put(k, WrappedByteArray.of(item.getData()));
      }
    });
    ((Flusher) db).flush(accounts);
    putCache(accounts);
    if (assets.size() > 0) {
      assetStore.updateByBatch(AccountAssetStore.convert(assets));
    }
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
