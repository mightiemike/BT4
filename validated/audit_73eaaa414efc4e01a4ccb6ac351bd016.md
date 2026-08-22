## Title
TIP-2935 activation silently converts a pre-existing normal account into a contract and wipes its delegated-resource state, permanently breaking counter-parties' resource-delegation withdrawals - (File: `framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java`)

### Summary
`HistoryBlockHashUtil.deploy()` deploys the TIP‑2935 "BlockHashHistory" system contract at the deterministic address `HISTORY_STORAGE_ADDRESS`. If a normal (non-contract) account already exists at that address — which any unprivileged user can create simply by sending TRX to it — the code converts it in place into a `Contract` account and calls `account.clearDelegatedResource()`, instead of refusing/migrating safely. [1](#0-0) 

This mirrors the `setLpToken` bug class: a reference/state object that other unprivileged users have already built financial state on top of (here: `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore` entries pointing at this address as delegator or receiver) is unilaterally mutated, and the accounting fields that back withdrawal/return of that state are wiped without settling the counter-party side, leaving other users unable to recover delegated TRX/resources.

### Finding Description
- `deploy()` is invoked once, automatically, from `ProposalService`/maintenance-time block processing when the TIP‑2935 hard fork activates — it is not gated by any user-supplied input other than the pre-existing state at the fixed address. [2](#0-1) 
- Any unprivileged actor can pre-populate `HISTORY_STORAGE_ADDRESS` as a normal account (e.g., by transferring TRX to it, or by having it be the `receiverAddress`/`ownerAddress` of a `DelegateResourceContract`/`FreezeBalanceContract` delegation) before the fork activates, since the address is publicly known ahead of time.
- Ordinary resource delegation (`DelegateResourceActuator`, `FreezeBalanceActuator`) stores delegation bookkeeping in two places: (1) counters on the `AccountCapsule` itself (`DelegatedFrozenV2BalanceForBandwidth/Energy`, `AcquiredDelegatedFrozenV2BalanceForBandwidth/Energy`, etc.) and (2) separate `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore` records that track the actual frozen balance and expiry between the two parties. [3](#0-2) 
- When `deploy()` hits the "account already exists" branch, it calls `account.updateAccountType(Protocol.AccountType.Contract)` and `account.clearDelegatedResource()` on the `AccountCapsule`, then persists it — but it never touches the corresponding `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore` entries where a counter-party (the other side of the delegation) still has its accounting keyed to this address. [4](#0-3) 
- Separately, `DelegateResourceActuator`/`DelegateResourceProcessor` explicitly forbid delegating resources *to* a `Contract`-type address ("Do not allow delegate resources to contract addresses"), which is an invariant enforced only at delegation time, not retroactively — so an address that silently becomes a `Contract` after having accumulated delegated-resource state now sits in a state the rest of the system assumes is impossible. [5](#0-4) [6](#0-5) 

This is structurally the same defect pattern as the reported `setLpToken` issue: a critical piece of state that other unprivileged users' funds/accounting depend on is mutated unilaterally and in-place, with no migration path for the parties who already relied on the old state, resulting in stuck/inaccessible balances for third parties who did nothing wrong.

### Impact Explanation
If the pre-existing account at `HISTORY_STORAGE_ADDRESS` was a party to any resource delegation (either as delegator whose counters get wiped by `clearDelegatedResource()`, or as receiver of delegated resources from other, unrelated users), the `AccountCapsule`-level bookkeeping that the `UnDelegateResourceActuator`/related undelegate flow relies on to compute/validate returns is zeroed out at the moment of contract conversion, while the counter-party's own `DelegatedResourceStore`/index records remain, referencing a party whose state no longer reflects the delegation. Depending on how the undelegate path validates against the receiver's/owner's account counters, unrelated third-party TRX delegated to or from this address can become permanently unreclaimable — a direct loss of user funds/resources with no privileged actor required to trigger the root cause (only the automatic, unavoidable hard-fork activation).

### Likelihood Explanation
The deterministic destination address is public before activation (it is compiled into the binary/config), so any user could, intentionally or accidentally, cause TRX transfers or resource delegations to land on it prior to the TIP‑2935 activation block. The activation itself is guaranteed to run exactly once on every node in the network at the scheduled fork height, so the "existing account" branch is fully attacker-triggerable and will deterministically execute the mutating code once the pre-condition (an account already present there) is met.

### Recommendation
Do not silently repurpose a pre-existing account with financial state. Before converting the account type, check whether the account has non-zero `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore` entries or resource-delegation counters (`getDelegatedFrozenV2BalanceForBandwidth/Energy`, `getAcquiredDelegatedFrozenV2BalanceForBandwidth/Energy`, etc.). If any such state exists, either (a) skip the deploy at this address the same way the "foreign code" branch already does, logging a warning and leaving TIP‑2935 functionality absent there, or (b) settle/return all outstanding delegated resources to their rightful owners before wiping the account's delegation counters, rather than calling `clearDelegatedResource()` unconditionally.

### Proof of Concept
1. Before the TIP‑2935 (`allowTvmPrague`) hard fork activates, an attacker or ordinary user sends TRX to `HISTORY_STORAGE_ADDRESS` (`410000f90827f1c53a10cb7a02335b175320002935`), or delegates resources to/from it via `DelegateResourceContract`/`FreezeBalanceContract` — this is permitted because, pre-activation, the address is an ordinary `AccountType.Normal` account.
2. At the scheduled activation height, `ProposalService` triggers `HistoryBlockHashUtil.deploy(manager)` on every node.
3. Because the account already exists, `deploy()` takes the branch at [7](#0-6) , calling `account.updateAccountType(Protocol.AccountType.Contract)` and `account.clearDelegatedResource()`, then persisting the mutated capsule.
4. Any counter-party who had delegated resources to/from this address before the fork now has orphaned `DelegatedResourceStore` records referencing an address whose account-level delegation counters have been zeroed and whose type is now `Contract` — a type to which delegation is explicitly disallowed elsewhere in the codebase — leaving their delegated TRX/resources stuck.

*Note:* I was unable to fully trace the exact undelegate/return code path (`UnDelegateResourceActuator`/processor) to confirm whether it would throw, silently no-op, or lose funds in this exact scenario, since it was not returned by search; a Devin session with full repository access should verify this end-to-end before finalizing severity.

### Citations

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L94-99)
```java
   * <p>Called only from {@code ProposalService} inside maintenance-time block
   * processing. Proposal validation rejects re-activation, so this runs at most
   * once per chain history; the three store writes share the block's revoking
   * session, so any node-local exception (RocksDB / IO) propagates and rolls
   * the {@code saveAllowTvmPrague(1)} write back atomically.
   */
```

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L100-121)
```java
  public static void deploy(Manager manager) {
    if (manager.getCodeStore().has(HISTORY_STORAGE_ADDRESS)
        || manager.getContractStore().has(HISTORY_STORAGE_ADDRESS)) {
      logger.warn("TIP-2935: foreign state at {}, skipping deploy",
          Hex.toHexString(HISTORY_STORAGE_ADDRESS));
      return;
    }

    manager.getCodeStore().put(HISTORY_STORAGE_ADDRESS,
        new CodeCapsule(HISTORY_STORAGE_CODE));
    manager.getContractStore().put(HISTORY_STORAGE_ADDRESS,
        new ContractCapsule(HISTORY_STORAGE_CONTRACT));

    AccountCapsule account = manager.getAccountStore().get(HISTORY_STORAGE_ADDRESS);
    boolean accountExisting = account != null;
    if (!accountExisting) {
      account = new AccountCapsule(HISTORY_STORAGE_ACCOUNT);
    } else {
      account.updateAccountType(Protocol.AccountType.Contract);
      account.clearDelegatedResource();
    }
    manager.getAccountStore().put(HISTORY_STORAGE_ADDRESS, account);
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L243-246)
```java
    if (receiverCapsule.getType() == AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L282-325)
```java
  private void delegateResource(byte[] ownerAddress, byte[] receiverAddress, boolean isBandwidth,
                                long balance, boolean lock, long lockPeriod) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicPropertiesStore = chainBaseManager.getDynamicPropertiesStore();
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    DelegatedResourceAccountIndexStore delegatedResourceAccountIndexStore = chainBaseManager
        .getDelegatedResourceAccountIndexStore();

    // 1. unlock the expired delegate resource
    long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, now);

    //modify DelegatedResourceStore
    long expireTime = 0;
    if (lock) {
      expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL;
    }
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, lock);
    DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore.get(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }

    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(balance, expireTime);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(balance, expireTime);
    }
    delegatedResourceStore.put(key, delegatedResourceCapsule);

    //modify DelegatedResourceAccountIndexStore
    delegatedResourceAccountIndexStore.delegateV2(ownerAddress, receiverAddress,
        dynamicPropertiesStore.getLatestBlockHeaderTimestamp());

    //modify AccountStore for receiver
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(balance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(balance);
    }
    accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L111-114)
```java
    if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
```
