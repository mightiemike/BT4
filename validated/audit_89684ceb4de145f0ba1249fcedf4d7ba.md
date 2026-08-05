### Title
Unbounded per-account TRC10 asset usage map growth causes O(N) unmetered work on every public `GetAccountResource`/`GetAccountNet` API call - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`AccountCapsule` stores a `free_asset_net_usage`/`free_asset_net_usageV2` map keyed by every distinct TRC10 asset ID the account has ever transacted with, and this map has no size cap. `Wallet.setAssetNetLimit`, called from both `Wallet.getAccountNet` and `Wallet.getAccountResource`, iterates the entire map and performs a store lookup (`AssetIssueStore`/`AssetIssueV2Store.get`) per entry every time the public HTTP/gRPC endpoint is invoked, so the per-request cost of these free, unauthenticated read endpoints scales linearly (and with real DB I/O) in the number of distinct assets the target account has ever touched.

### Finding Description
`BandwidthProcessor.useAssetAccountNet` (chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java, `updateUsage`/`useAssetAccountNet`) adds an entry to the account's `freeAssetNetUsage(V2)` map for every distinct asset the account participates in via ordinary, unprivileged `TransferAssetContract`/`ParticipateAssetIssueContract` transactions: [1](#0-0) 
There is no cap anywhere in the actuator/processor code on the number of distinct assets tracked per account — no `MAX_ASSET` style guard was found in any actuator.

Every call to `Wallet.getAccountResource`/`Wallet.getAccountNet` (reachable from the public, unauthenticated `GetAccountResourceServlet`/`GetAccountNetServlet` HTTP endpoints and the corresponding gRPC calls) invokes `setAssetNetLimit`, which iterates `accountCapsule.getAllFreeAssetNetUsage()`/`getAllFreeAssetNetUsageV2()` and does one store `.get()` lookup per map entry to build `assetNetLimitMap`: [2](#0-1) [3](#0-2) 

The HTTP servlets pass through directly to `Wallet.getAccountResource`/`getAccountNet` with no size limiting or pagination on the resulting maps: [4](#0-3) 

Because the asset-usage map keys are attacker-influenced (any address that receives/sends N distinct TRC10 assets accumulates N map entries permanently, since there is no eviction), an attacker who owns or controls an address can drive N arbitrarily high by transacting (as sender or, more cheaply, as recipient of unsolicited `TransferAssetContract`s from other unprivileged accounts, or by participating in N different asset issues) across N distinct existing TRC10 tokens. TRON currently has thousands of issued TRC10 assets, so N is not inherently bounded by protocol design. Once that account's map is large, every future call to `getaccountresource`/`getaccountnet` for that address — issued by any caller, not just the attacker — costs O(N) map iteration plus O(N) DB reads, with no per-request cost/fee charged to the caller (these are free public read APIs, only rate-limited by `RateLimiterServlet`, which limits request rate, not per-request cost).

### Impact Explanation
This is a "public-cost bypass" DoS: building the oversized account state costs the attacker only the normal transaction fees for N ordinary transfers/participations (a bounded, attacker-paid cost), but the resulting per-request cost of serving that account's resource info is unbounded and paid by any node serving public HTTP/gRPC traffic, for every subsequent request, indefinitely. Repeated crafted requests against the same poisoned address can degrade node CPU/I-O for read-only, unauthenticated endpoints, a classic amplification/DoS vector against public FullNode API infrastructure.

### Likelihood Explanation
Feasible with only unprivileged capabilities: `TransferAssetContract` and `ParticipateAssetIssueContract` are ordinary transactions available to any account holder, and TRC10 assets already number in the thousands on TRON mainnet, so an attacker does not even need to create new assets — they can participate in N pre-existing assets. The resulting poisoned account state is permanent (no expiry/cleanup of the usage map), making the attack a one-time setup cost with repeatable, unbounded amplification on every subsequent public API query.

### Recommendation
Bound the number of distinct assets tracked per account (e.g., cap `freeAssetNetUsage`/`freeAssetNetUsageV2` map size in the actuator/processor, or evict expired/zero entries), and/or cap or paginate the `assetNetUsed`/`assetNetLimit` map returned by `Wallet.getAccountResource`/`getAccountNet`, and/or move the per-asset limit lookups in `setAssetNetLimit` to avoid an unbounded number of store reads per public API request (e.g., cache `AssetIssueStore` limits, or restrict how many assets are reported per call).

### Proof of Concept
Java integration test plan (extends `WalletTest`/`GetAccountResourceServletTest` pattern):
1. Create an `AccountCapsule` for `OWNER_ADDRESS` and N synthetic `AssetIssueCapsule`s (e.g., N = 10,000) in `chainBaseManager.getAssetIssueV2Store()`.
2. For each synthetic asset ID, call `accountCapsule.putFreeAssetNetUsageV2(id, someUsage)` and `putLatestAssetOperationTimeMapV2(id, time)` to simulate the state produced by N real `ParticipateAssetIssueContract`/`TransferAssetContract` transactions (mirroring what `BandwidthProcessor.useAssetAccountNet` does), then persist via `accountStore.put`.
3. Measure wall-clock time of repeated calls to `wallet.getAccountResource(address)` (or `GetAccountResourceServlet.doGet`) for increasing N (e.g., 100, 1,000, 10,000).
4. Assert that call latency/DB read count scales linearly with N and is not capped by any constant, i.e. `assertTrue(latency(N=10000) >> latency(N=100) * some_bounded_factor)`, demonstrating the absence of a cap — thereby confirming the reported unbounded per-request cost.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L400-406)
```java
    if (chainBaseManager.getDynamicPropertiesStore().getAllowSameTokenName() == 0) {
      accountCapsule.putLatestAssetOperationTimeMap(tokenName,
          latestAssetOperationTime);
      accountCapsule.putFreeAssetNetUsage(tokenName, newFreeAssetNetUsage);
      accountCapsule.putLatestAssetOperationTimeMapV2(tokenID,
          latestAssetOperationTime);
      accountCapsule.putFreeAssetNetUsageV2(tokenID, newFreeAssetNetUsage);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1591-1610)
```java
  private Map<String, Long> setAssetNetLimit(Map<String, Long> assetNetLimitMap,
      AccountCapsule accountCapsule) {
    Map<String, Long> allFreeAssetNetUsage;
    if (chainBaseManager.getDynamicPropertiesStore().getAllowSameTokenName() == 0) {
      allFreeAssetNetUsage = accountCapsule.getAllFreeAssetNetUsage();
      allFreeAssetNetUsage.keySet().forEach(asset -> {
        byte[] key = ByteArray.fromString(asset);
        assetNetLimitMap
            .put(asset, chainBaseManager.getAssetIssueStore().get(key).getFreeAssetNetLimit());
      });
    } else {
      allFreeAssetNetUsage = accountCapsule.getAllFreeAssetNetUsageV2();
      allFreeAssetNetUsage.keySet().forEach(asset -> {
        byte[] key = ByteArray.fromString(asset);
        assetNetLimitMap
            .put(asset, chainBaseManager.getAssetIssueV2Store().get(key).getFreeAssetNetLimit());
      });
    }
    return allFreeAssetNetUsage;
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1646-1705)
```java
  public AccountResourceMessage getAccountResource(ByteString accountAddress) {
    if (accountAddress == null || accountAddress.isEmpty()) {
      return null;
    }
    AccountResourceMessage.Builder builder = AccountResourceMessage.newBuilder();
    AccountCapsule accountCapsule =
        chainBaseManager.getAccountStore().get(accountAddress.toByteArray());
    if (accountCapsule == null) {
      return null;
    }

    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    processor.updateUsage(accountCapsule);

    EnergyProcessor energyProcessor = new EnergyProcessor(
        chainBaseManager.getDynamicPropertiesStore(),
        chainBaseManager.getAccountStore());
    energyProcessor.updateUsage(accountCapsule);

    long netLimit = processor
        .calculateGlobalNetLimit(accountCapsule);
    long freeNetLimit = chainBaseManager.getDynamicPropertiesStore().getFreeNetLimit();
    long totalNetLimit = chainBaseManager.getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = chainBaseManager.getDynamicPropertiesStore().getTotalNetWeight();
    long totalTronPowerWeight = chainBaseManager.getDynamicPropertiesStore()
        .getTotalTronPowerWeight();
    long energyLimit = energyProcessor
        .calculateGlobalEnergyLimit(accountCapsule);
    long totalEnergyLimit =
        chainBaseManager.getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight =
        chainBaseManager.getDynamicPropertiesStore().getTotalEnergyWeight();

    long storageLimit = accountCapsule.getAccountResource().getStorageLimit();
    long storageUsage = accountCapsule.getAccountResource().getStorageUsage();
    long allTronPowerUsage = accountCapsule.getTronPowerUsage();
    long allTronPower = accountCapsule.getAllTronPower() / TRX_PRECISION;

    Map<String, Long> assetNetLimitMap = new HashMap<>();
    Map<String, Long> allFreeAssetNetUsage = setAssetNetLimit(assetNetLimitMap, accountCapsule);

    builder.setFreeNetUsed(accountCapsule.getFreeNetUsage())
        .setFreeNetLimit(freeNetLimit)
        .setNetUsed(accountCapsule.getNetUsage())
        .setNetLimit(netLimit)
        .setTotalNetLimit(totalNetLimit)
        .setTotalNetWeight(totalNetWeight)
        .setTotalTronPowerWeight(totalTronPowerWeight)
        .setEnergyLimit(energyLimit)
        .setEnergyUsed(accountCapsule.getAccountResource().getEnergyUsage())
        .setTronPowerUsed(allTronPowerUsage)
        .setTronPowerLimit(allTronPower)
        .setTotalEnergyLimit(totalEnergyLimit)
        .setTotalEnergyWeight(totalEnergyWeight)
        .setStorageLimit(storageLimit)
        .setStorageUsed(storageUsage)
        .putAllAssetNetUsed(allFreeAssetNetUsage)
        .putAllAssetNetLimit(assetNetLimitMap);
    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetAccountResourceServlet.java (L21-57)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String address = request.getParameter("address");
      if (visible) {
        address = Util.getHexAddress(address);
      }
      fillResponse(visible, ByteString.copyFrom(ByteArray.fromHexString(address)), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      JSONObject jsonObject = JSONObject.parseObject(params.getParams());
      String address = jsonObject.getString("address");
      if (params.isVisible()) {
        address = Util.getHexAddress(address);
      }
      fillResponse(params.isVisible(), ByteString.copyFrom(ByteArray.fromHexString(address)),
          response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(boolean visible, ByteString address, HttpServletResponse response)
      throws Exception {
    AccountResourceMessage reply = wallet.getAccountResource(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```
