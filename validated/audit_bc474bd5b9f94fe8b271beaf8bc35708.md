## Analysis: Unmetered iteration over unbounded per-account TRC10 asset map (analog found)

### Title
Unmetered linear iteration over an unbounded per-account TRC10 asset map in bandwidth accounting — (File: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`)

### Summary
`BandwidthProcessor.updateUsage(AccountCapsule accountCapsule)` iterates over the *entire* set of TRC10 asset balances held by an account (`accountCapsule.getAssetMap()` / `accountCapsule.getAssetMapV2()`) every time bandwidth usage is recalculated for that account, with no cap on the number of distinct asset entries and no extra resource/gas cost that scales with that count. [1](#0-0) 

### Finding Description
`updateUsage` loops with `assetMap.forEach(...)` over `accountCapsule.getAssetMap()` (legacy, when `allowSameTokenName==0`) and unconditionally over `accountCapsule.getAssetMapV2()` merged with `getAllFreeAssetNetUsageV2()`, recomputing per‑asset free bandwidth usage for every entry: [2](#0-1) 

`getAssetMapV2()` in `AccountCapsule` simply returns the full map of every distinct TRC10 token ID the account has ever held a nonzero (or historically nonzero) balance of, with no size limit enforced anywhere in the class: [3](#0-2) 

This map grows any time the account receives a `TransferAssetContract` for a new token ID (`addAssetV2`/`putAssetV2`), and there is no protocol-level limit found on the number of *distinct* TRC10 tokens a single account may hold — asset issuance itself only requires paying `AssetIssueFee`/paying the create-fee (see `AssetIssueActuator`), and transferring a tiny amount of many different self-issued (or third-party) tokens into a target account costs the sender normal transfer fees/bandwidth, not the receiver. Since `updateUsage(AccountCapsule)` is invoked broadly — including from numerous `Wallet.java` account-query paths and from bandwidth consumption logic on every transaction touching that account — an attacker can create a large number of TRC10 assets and drip-transfer them into a single account to inflate that account's `AssetV2Map` to an arbitrarily large size. Every subsequent state transition or RPC call that triggers `updateUsage` on that account then pays an unbounded, unmetered linear cost, without any additional bandwidth/energy fee proportional to the number of asset entries processed.

This is structurally identical to the reported bug class: an actuator/accounting routine performs **linear iteration over an attacker-controlled, unbounded per-account collection of token denominations**, with **no cap and no metering that scales with collection size**, creating a resource-exhaustion (chain halt / DoS) vector reachable through ordinary, unprivileged transactions (`TransferAssetContract`, `AssetIssueContract`).

### Impact Explanation
If exploited, processing a transaction (or answering certain read APIs) for a victim/attacker account with a very large `AssetV2Map` becomes disproportionately expensive relative to the bandwidth/energy paid, degrading block production performance for all nodes that must re-execute the transaction, and potentially causing block processing timeouts — a consensus-affecting DoS/chain-halt vector, consistent with "concrete... DoS via... protocol implementation" in scope.

### Likelihood Explanation
Likelihood is currently **uncertain/likely low-to-moderate** rather than proven high: I was not able to find, within index limits, an explicit protocol-level cap on the number of distinct TRC10 asset IDs a single account may accumulate, nor a fee that scales with asset-map size in `AssetIssueActuator`/`TransactionUtil`. Creating and distributing many TRC10 tokens does cost TRX (issuance fee) and bandwidth (per transfer), which raises the cost of the attack somewhat, similar to the original report's "requires spam token creation" caveat — but nothing prevents it structurally. Given the index size limits, I could not fully confirm whether any newer chain parameter (analogous to the "restakable denom allowlist" in the original report) restricts this in later protocol versions; a full code review (e.g., via a Devin session with full repo access) would be needed to confirm whether any such cap exists elsewhere (e.g., in `AssetIssueActuator`, `TransactionUtil`, or dynamic properties limiting total TRC10 tokens system-wide vs. per-account).

### Recommendation
- Cap the number of distinct TRC10 asset entries (`AssetV2Map`) a single account may hold, similar to `MAX_VOTE_NUMBER` capping used for `VoteWitnessProcessor` (`actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java:33-36`), which is the pattern already used elsewhere in this codebase to bound analogous unbounded lists. [4](#0-3) 
- Alternatively, charge bandwidth/energy proportional to the number of asset-map entries processed in `BandwidthProcessor.updateUsage(AccountCapsule)` so cost scales with iteration size.

### Proof of Concept
Conceptual (not executed):
1. Attacker issues N TRC10 tokens via `AssetIssueContract` (paying the issuance fee for each).
2. Attacker sends `TransferAssetContract` for each of the N tokens into a single target account (own account), inflating `AssetV2Map` size to N.
3. Every subsequent transaction from/to that account, or RPC calls that invoke `BandwidthProcessor.updateUsage`, incur an O(N) `forEach` cost in `updateUsage` with no additional fee, at [5](#0-4) 
4. Repeating at scale across many accounts amplifies per-transaction/per-block processing cost network-wide.

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

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L878-885)
```java
  public Map<String, Long> getAssetMapV2() {
    importAllAsset();
    Map<String, Long> assetMap = this.account.getAssetV2Map();
    if (assetMap.isEmpty()) {
      assetMap = Maps.newHashMap();
    }
    return assetMap;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L28-37)
```java
  public void validate(VoteWitnessParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    if (param.getVotes().size() > MAX_VOTE_NUMBER) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + MAX_VOTE_NUMBER);
    }
  }
```
