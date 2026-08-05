Based on my investigation, I have enough context to answer this.

### Title
Unbounded `AssetIssueList` allocation via `GetAssetIssueListByNameServlet` when duplicate token names are permitted - ([File: framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java])

### Summary
When `AllowSameTokenName` is enabled, `Wallet.getAssetIssueListByName` filters the entire in-memory `AssetIssueCapsule` list by name equality with no result cap, unlike `AssetIssueStore.getAssetIssuesPaginated` which enforces `ASSET_ISSUE_COUNT_LIMIT_MAX` [1](#0-0) . This means a single unprivileged HTTP/gRPC request can force the node to build and JSON-serialize an `AssetIssueList` proportional to however many same-named assets exist on chain.

### Finding Description
`GetAssetIssueListByNameServlet.fillResponse` calls `wallet.getAssetIssueListByName(...)` and directly serializes the full result with `JsonFormat.printToString(reply, visible)` with no size limiting [2](#0-1) . `Wallet.getAssetIssueListByName` streams over `getAllAssetIssues()` and appends every capsule whose name matches into an `AssetIssueList.Builder` with no upper bound on `builder.getAssetIssueCount()` [3](#0-2) . By contrast, the paginated variant `AssetIssueStore.getAssetIssuesPaginated` explicitly clamps `limit` to `ASSET_ISSUE_COUNT_LIMIT_MAX` [1](#0-0) , showing the codebase is aware of this limit pattern but did not apply it here.

However, creating duplicate-name assets is not free or trivially repeatable by a single attacker. `AssetIssueActuator.validate()` requires a distinct, valid on-chain account per issuance (enforced elsewhere as "An account can only issue one asset" per `AssetIssueActuatorTest.assetIssueNameTest`), and each successful issuance charges `dynamicStore.getAssetIssueFee()` to the blackhole address, confirmed across multiple actuator tests (`SameTokenNameOpenAssetIssueSuccess`, `IssueSameTokenNameAssert`) [4](#0-3) . So to create K duplicate-name assets, an attacker needs K funded accounts each paying the asset-issue fee and bandwidth/energy costs for a full transaction — this is not "free" unbounded amplification, but a paid, linearly-costed precondition.

### Impact Explanation
Once K duplicate-named assets exist on chain (regardless of who created them or when), any unprivileged caller can repeatedly hit `GetAssetIssueListByNameServlet` (or the equivalent gRPC `GetAssetIssueListByName`) with that name and force the node to allocate and serialize an `AssetIssueList` of size K on every single request, with no per-request cost to the caller (read-only, rate-limited by `RateLimiterServlet` only, not capped in size). This is a repeated-amplification / resource-exhaustion vector: the one-time paid cost of creating K assets is incurred once, but the O(K) serialization cost is incurred on every subsequent read request by any anonymous caller.

### Likelihood Explanation
Feasible only if `AllowSameTokenName` is enabled (a chain-parameter governed by committee/witness voting, not attacker-controlled) and requires the attacker to fund and issue K asset transactions (each requiring a distinct account, the `getAssetIssueFee()` TRX cost, and normal transaction bandwidth/energy) to seed the duplicate-name data set. After that one-time cost, the read-amplification is repeatable indefinitely and free per read request.

### Recommendation
Apply the same `ASSET_ISSUE_COUNT_LIMIT_MAX`-style cap used in `AssetIssueStore.getAssetIssuesPaginated` to `Wallet.getAssetIssueListByName`, or require pagination parameters instead of returning an unbounded list, to match the existing pattern in the codebase.

### Proof of Concept
```java
// Integration test (extends existing AssetIssueActuatorTest / WalletTest patterns)
@Test
public void unboundedGetAssetIssueListByNameTest() throws Exception {
  dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(1);
  int K = 5000;
  for (int i = 0; i < K; i++) {
    // fund a fresh account, build+validate+execute an AssetIssueActuator
    // with the SAME name "DUPTOKEN" but a unique owner address each time,
    // paying dbManager.getDynamicPropertiesStore().getAssetIssueFee() from blackhole test setup
  }
  AssetIssueList reply = wallet.getAssetIssueListByName(ByteString.copyFromUtf8("DUPTOKEN"));
  Assert.assertEquals(K, reply.getAssetIssueCount()); // no cap applied
  String json = JsonFormat.printToString(reply, true);
  Assert.assertTrue(json.length() > 1_000_000); // response grows unbounded with K
}
```
Expected assertion: `reply.getAssetIssueCount()` and serialized JSON size grow linearly with `K` with no server-side truncation, confirmed by comparing against `AssetIssueStore.getAssetIssuesPaginated`'s capped behavior.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java (L55-55)
```java
    limit = limit > ASSET_ISSUE_COUNT_LIMIT_MAX ? ASSET_ISSUE_COUNT_LIMIT_MAX : limit;
```

**File:** framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java (L45-56)
```java
  private void fillResponse(boolean visible, String value, HttpServletResponse response)
      throws IOException {
    if (visible) {
      value = Util.getHexString(value);
    }
    AssetIssueList reply = wallet.getAssetIssueListByName(ByteString.copyFrom(
        ByteArray.fromHexString(value)));
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1774-1795)
```java
  public AssetIssueList getAssetIssueListByName(ByteString assetName) {
    if (assetName == null || assetName.isEmpty()) {
      return null;
    }

    List<AssetIssueCapsule> assetIssueCapsuleList =
        getAssetIssueStoreFinal(chainBaseManager.getDynamicPropertiesStore(),
            chainBaseManager.getAssetIssueStore(),
            chainBaseManager.getAssetIssueV2Store()).getAllAssetIssues();

    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    AssetIssueList.Builder builder = AssetIssueList.newBuilder();
    assetIssueCapsuleList.stream()
        .filter(assetIssueCapsule -> assetIssueCapsule.getName().equals(assetName))
        .forEach(
            issueCapsule -> {
              processor.updateUsage(issueCapsule);
              builder.addAssetIssue(issueCapsule.getInstance());
            });

    return builder.build();
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java (L196-212)
```java
  @Test
  public void SameTokenNameOpenAssetIssueSuccess() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(1);
    AssetIssueActuator actuator = new AssetIssueActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract());

    TransactionResultCapsule ret = new TransactionResultCapsule();
    Long blackholeBalance = dbManager.getAccountStore().getBlackhole().getBalance();
    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.assertEquals(ret.getInstance().getRet(), code.SUCESS);
      AccountCapsule owner = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(owner.getBalance(), 0L);
      Assert.assertEquals(dbManager.getAccountStore().getBlackhole().getBalance(),
          blackholeBalance + dbManager.getDynamicPropertiesStore().getAssetIssueFee());
```
