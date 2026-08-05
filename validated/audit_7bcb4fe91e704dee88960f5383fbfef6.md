Found a concrete analog: the smart-contract-facing "max unfreezable/available" opcode helper `FreezeV2Util.queryUnfreezableBalanceV2()` returns a value that is disconnected from what the actual unfreeze actuator will accept, exactly mirroring the `maxWithdraw()`/`totalReleasedAssets` divergence in the GoGoPool report (a view-style "how much can I unfreeze" helper that uses a different accounting basis than the state-mutating operation that later validates and executes the withdrawal).

### Title
`queryUnfreezableBalanceV2()` reports frozen amount as unfreezable without reflecting `UNFREEZE_MAX_TIMES` / pending-unfreeze-slot exhaustion, causing on-chain callers to build unfreeze calls that revert - (File: `actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java`)

### Summary
The TVM native-contract helper `FreezeV2Util.queryUnfreezableBalanceV2(address, type, repository)` [1](#0-0)  is exposed to smart contracts (via TVM opcode) as the "how much can this account still unfreeze" query. It simply returns the account's current frozen balance for the resource type (`getFrozenV2BalanceForBandwidth()`, `getFrozenV2BalanceForEnergy()`, or `getTronPowerFrozenV2Balance()`), with no reference to whether the account can actually perform another unfreeze operation right now.

### Finding Description
The actual state-changing path, `UnfreezeBalanceV2Processor.validate()` (called by both the `UnfreezeBalanceV2Actuator` and its native-contract TVM counterpart), enforces an entirely separate, unrelated constraint before allowing any unfreeze: the number of pending (non-expired) unfreeze operations must be below `UNFREEZE_MAX_TIMES` (32), checked via `accountCapsule.getUnfreezingV2Count(now)` [2](#0-1) . If this limit is reached, `validate()` throws `ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit")`, and the whole unfreeze call reverts — this check is completely independent of the frozen balance amount checked in `checkUnfreezeBalance()` [3](#0-2) .

The codebase does provide a *separate* helper, `queryAvailableUnfreezeV2Size()`, that correctly reflects the slot-exhaustion constraint [4](#0-3) , but `queryUnfreezableBalanceV2()` (the "how much AVAX/TRX-equivalent can I unfreeze" analog of `maxWithdraw`) does not consult it. This is structurally identical to the GoGoPool bug: `maxWithdraw()` returned an amount derived from `totalAssets()` (deposits + rewards) while the actual withdrawal path is gated by a stricter, independently-tracked variable (`totalReleasedAssets`), causing `beforeWithdraw()` to underflow and revert. Here, `queryUnfreezableBalanceV2()` returns an amount derived purely from frozen balance while the actual unfreeze path is gated by a stricter, independently-tracked counter (pending unfreeze slots), causing `validate()` to reject the transaction.

### Impact Explanation
A smart contract that composes `queryUnfreezableBalanceV2()` to determine "how much can I unfreeze right now" and then immediately issues an `UnfreezeBalanceV2` native call for that full amount will have the transaction revert whenever the account already has 32 pending unfreeze operations, even though the view function reported a large non-zero unfreezable amount. This breaks composability for any protocol/vault contract built on top of TRON's native staking (analogous to GoGoPool's ERC4626 vault composability breaking), wasting gas and causing downstream automated systems (bots, wallets, other contracts) to fail unexpectedly. This matches the accepted Medium-severity impact class in the original report: a documented, confirmed inconsistency between a "max amount" view helper and the actual execution path, with concrete revert impact — not merely theoretical.

### Likelihood Explanation
Any account/contract that freezes and unfreezes resources frequently (e.g., automated resource-management or delegation contracts, which are a common pattern on TRON for energy/bandwidth rental) can accumulate up to 32 pending unfreeze entries within `unfreezeDelayDays`. Because `queryUnfreezableBalanceV2()` is a public TVM opcode helper intended precisely for such contracts to self-check before calling unfreeze, and it never reflects the 32-slot cap, hitting this divergence requires only ordinary, unprivileged usage patterns (frequent unfreeze cycles), not any special trust or attack setup.

### Recommendation
Have `queryUnfreezableBalanceV2()` also account for the unfreeze-slot limit — e.g., return `0` (or clamp) when `accountCapsule.getUnfreezingV2Count(now) >= UNFREEZE_MAX_TIMES`, mirroring the check already performed independently by `queryAvailableUnfreezeV2Size()` and `UnfreezeBalanceV2Processor.validate()`. Alternatively, document clearly that callers must additionally check `queryAvailableUnfreezeV2Size() > 0` before relying on `queryUnfreezableBalanceV2()`, and align both queries so a single "max unfreezable amount" call is safe to compose with an immediate unfreeze execution.

### Proof of Concept
1. Contract `C` freezes `BANDWIDTH` balance, then repeatedly calls `unfreezeBalanceV2` with small amounts in 32 separate transactions before any of them expire (`unfreezeDelayDays` not yet elapsed) — reaching `unfreezingV2Count == UNFREEZE_MAX_TIMES` as tracked by `accountCapsule.getUnfreezingV2Count(now)`.
2. Contract `C` then calls the TVM opcode wired to `FreezeV2Util.queryUnfreezableBalanceV2(C, BANDWIDTH_TYPE, repo)` [5](#0-4) , which returns the full remaining frozen balance (e.g., `1000 TRX`), since it only reads `getFrozenV2BalanceForBandwidth()` and ignores the pending-unfreeze count.
3. `C` then issues an `unfreezeBalanceV2` call for that reported amount.
4. `UnfreezeBalanceV2Processor.validate()` computes `unfreezingCount = accountCapsule.getUnfreezingV2Count(now)` and, since `32 <= unfreezingCount`, throws `ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit")` [2](#0-1) , reverting the transaction despite `queryUnfreezableBalanceV2()` having reported the amount as unfreezable.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L39-65)
```java
  public static long queryUnfreezableBalanceV2(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0;
    }

    // BANDWIDTH
    if (type == 0) {
      return accountCapsule.getFrozenV2BalanceForBandwidth();
    }

    // ENERGY
    if (type == 1) {
      return accountCapsule.getFrozenV2BalanceForEnergy();
    }

    // POWER
    if (type == 2) {
      return accountCapsule.getTronPowerFrozenV2Balance();
    }

    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L126-140)
```java
  public static long queryAvailableUnfreezeV2Size(byte[] address, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0L;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0L;
    }

    long now = repository.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    int unfreezingV2Count = accountCapsule.getUnfreezingV2Count(now);
    return max(UnfreezeBalanceV2Actuator.getUNFREEZE_MAX_TIMES() - unfreezingV2Count, 0L,
        VMConfig.disableJavaLangMath());
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L50-54)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    int unfreezingCount = accountCapsule.getUnfreezingV2Count(now);
    if (UnfreezeBalanceV2Actuator.getUNFREEZE_MAX_TIMES() <= unfreezingCount) {
      throw new ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L91-106)
```java
  private boolean checkUnfreezeBalance(
      AccountCapsule accountCapsule, long unfreezeBalance, Common.ResourceCode freezeType)  {
    if (unfreezeBalance <= 0) {
      return false;
    }
    long frozenBalance = 0L;
    List<Protocol.Account.FreezeV2> freezeV2List = accountCapsule.getFrozenV2List();
    for (Protocol.Account.FreezeV2 freezeV2 : freezeV2List) {
      if (freezeV2.getType().equals(freezeType)) {
        frozenBalance = freezeV2.getAmount();
        break;
      }
    }

    return unfreezeBalance <= frozenBalance;
  }
```
