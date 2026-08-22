### Title
Unbounded loop over TRC10 asset map in `MUtil.transferAllToken()` allows gas-underpriced DoS on SELFDESTRUCT - (File: actuator/src/main/java/org/tron/core/vm/utils/MUtil.java)

### Summary
The C4 finding describes `SurplusGuildMinter.getReward()` looping over an unbounded, attacker-growable collection (all gauges/terms a user is subscribed to) with no per-iteration gas accounting matching real cost, enabling an Out-Of-Gas DoS. The closest reachable analog in java-tron is `MUtil.transferAllToken()`, invoked from the TVM `SUICIDE`/`SUICIDE2` opcode handlers in `Program.suicide()`/`Program.suicide2()`, which iterates over the *entire* `AssetMapV2` (all distinct TRC10 token balances) of the self-destructing account while the opcode itself is charged a fixed energy cost.

### Finding Description
When a smart contract executes `SELFDESTRUCT`, `Program.suicide()` (and `suicide2()`) call `MUtil.transferAllToken(getContractState(), owner, obtainer)` when `VMConfig.allowTvmTransferTrc10()` is enabled: [1](#0-0) 

`transferAllToken` then iterates over `fromAccountCap.getAssetMapV2()` with `forEach`, moving every token balance the account holds to the target address: [2](#0-1) 

Unlike `LendingTerm`/gauge onboarding in the referenced report (gated by governance vote quorum), there is no protocol-level cap on the number of *distinct* TRC10 token IDs that can accumulate in a single account's asset map: any third party can issue a new TRC10 asset and transfer a tiny (even zero-relevant) amount to a victim/attacker-controlled contract address via ordinary `TransferAssetContract` or TVM token transfers, each addition growing `AssetV2Map` by one entry (`AccountCapsule.addAssetAmountV2` / `importAsset` populate `AssetV2Map`, see `AccountCapsule.getAssetMapV2()`): [3](#0-2) 

Since a single attacker fully controls their own contract account, they can trivially self-issue and self-send an arbitrarily large number of distinct TRC10 asset types to that contract (bounded only by their own willingness to pay ordinary transfer fees, which are flat per-transaction and independent of the resulting asset-map size), then trigger `SELFDESTRUCT` on it. The `SUICIDE` opcode's energy cost, defined in `EnergyCost.java`, is a fixed base cost (plus optional new-account surcharge) — it does not scale with the number of asset entries that must be copied/iterated in `transferAllToken`.

### Impact Explanation
Executing `SELFDESTRUCT` on an account holding a very large number of distinct TRC10 balances forces the node to perform O(n) map iteration and protobuf builder mutation (`toBuilder().putAssetV2(...)` for every entry) for a transaction that only pays the fixed `SUICIDE` energy price. This is a real, EnergyCost-vs-actual-work mismatch: the transaction consumes disproportionate CPU/heap relative to energy burned, and if n is large enough the block-processing time for that single transaction could stall block production or blow past per-block CPU budget — a resource-exhaustion / DoS condition analogous to the reported unbounded-loop issue, reachable from an ordinary, unprivileged, user-broadcast smart-contract transaction (no admin/governance action required).

### Likelihood Explanation
Moderate/uncertain. The attacker must first accumulate a large number of distinct TRC10 asset entries on the target contract account, which requires many prior ordinary transactions (asset issuance + transfer), each individually rate-limited by normal bandwidth/energy fees; there is no single-transaction way to inflate the asset map. This raises the cost/effort bar compared to the original C4 report (where governance-gated `LendingTermOnboarding` could add unbounded gauges), similar to how C4 judges/wardens debated likelihood for the original finding. I was not able to fully verify (within available tool budget) whether `EnergyCost.java`'s `SUICIDE` cost calculation includes any factor proportional to asset-map size, nor whether `Repository`/protobuf builder operations in `transferAllToken` have been optimized elsewhere to bound this cost — this should be confirmed by inspecting `actuator/src/main/java/org/tron/core/vm/EnergyCost.java` in full and any related benchmark/perf tests.

### Recommendation
- Charge energy for `SUICIDE` proportional to the number of asset entries transferred (e.g., an incremental cost per TRC10 balance moved), similar to memory-expansion or storage-write metering.
- Alternatively/additionally, impose an explicit maximum number of distinct TRC10 assets a single account may accumulate, or require the `SELFDESTRUCT`-triggered token sweep to be capped/batched rather than performed unconditionally in one call.
- Add a fuzz/benchmark test that creates a contract holding a very large number of distinct TRC10 balances and measures wall-clock time and consumed vs. charged energy for `SELFDESTRUCT`.

### Proof of Concept
Conceptual PoC (not executed, due to tool limitations in this environment):
1. Deploy a contract `Victim`.
2. From many different accounts, issue N distinct TRC10 tokens and transfer 1 unit of each to `Victim`'s address, populating `AssetV2Map` with N entries.
3. Call `Victim.selfdestruct(target)` (with `allowTvmTransferTrc10` enabled), triggering `Program.suicide()` → `MUtil.transferAllToken()`.
4. Measure that execution time/CPU for this single `SELFDESTRUCT` transaction grows linearly with N while the energy charged for the `SUICIDE` opcode remains constant, demonstrating the metering mismatch.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L474-488)
```java
    if (FastByteComparisons.compareTo(owner, 0, ADDRESS_SIZE, obtainer, 0, ADDRESS_SIZE) == 0) {
      // if owner == obtainer just zeroing account according to Yellow Paper
      getContractState().addBalance(owner, -balance);
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (VMConfig.allowTvmTransferTrc10()) {
        getContractState().addBalance(blackHoleAddress, balance);
        MUtil.transferAllToken(getContractState(), owner, blackHoleAddress);
      }
    } else {
      createAccountIfNotExist(getContractState(), obtainer);
      try {
        MUtil.transfer(getContractState(), owner, obtainer, balance);
        if (VMConfig.allowTvmTransferTrc10()) {
          MUtil.transferAllToken(getContractState(), owner, obtainer);
        }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/MUtil.java (L28-41)
```java
  public static void transferAllToken(Repository deposit, byte[] fromAddress, byte[] toAddress) {
    AccountCapsule fromAccountCap = deposit.getAccount(fromAddress);
    Protocol.Account.Builder fromBuilder = fromAccountCap.getInstance().toBuilder();
    AccountCapsule toAccountCap = deposit.getAccount(toAddress);
    toAccountCap.importAllAsset();
    Protocol.Account.Builder toBuilder = toAccountCap.getInstance().toBuilder();
    fromAccountCap.getAssetMapV2().forEach((tokenId, amount) -> {
      toBuilder.putAssetV2(tokenId, toBuilder.getAssetV2Map().getOrDefault(tokenId, 0L) + amount);
      fromBuilder.putAssetV2(tokenId, 0L);
    });

    deposit.putAccountValue(fromAddress, new AccountCapsule(fromBuilder.build()));
    deposit.putAccountValue(toAddress, new AccountCapsule(toBuilder.build()));
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
