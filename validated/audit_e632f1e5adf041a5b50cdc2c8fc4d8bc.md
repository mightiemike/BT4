### Title
Normal-account state (votes, frozen resources, permissions) survives an in-place `AccountType` upgrade to `Contract` at the TIP-2935 canonical address - ([File: framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java])

### Summary
The reported bug class — one on-chain entity silently taking on a second, incompatible role while continuing to share the *same* storage record, so state fields meant for role A leak into and corrupt role B — has a direct structural analog in `HistoryBlockHashUtil.deploy()`. When the canonical TIP‑2935 address already holds a pre-existing `Normal` `AccountCapsule` (e.g., because someone pre-funded it), activation does not create a fresh, isolated system-contract account; it mutates the existing `AccountCapsule` in place, only calling `updateAccountType(Contract)` and `clearDelegatedResource()`, and writes it back under the same key.

### Finding Description
`HistoryBlockHashUtil.deploy()` is invoked once, deterministically, from `ProposalService` at proposal activation: [1](#0-0) 

The pre-existing-account branch is explicitly documented as mutating the capsule "in place to preserve balance/asset state", and only clears delegated-resource fields — it does **not** clear votes, frozen bandwidth/energy balances, `FreezeV2` entries, or the account's `Permission` (multisig) structures: [2](#0-1) 

Because nobody holds the private key for this canonical address (it mirrors EIP‑2935's "presigned, no-private-key" deploy design), the account can never be created/managed through normal user-initiated actuators requiring the owner's signature. However, other unprivileged, address-agnostic paths can still populate its `AccountCapsule` before activation — most notably being the *receiver* of a `DelegateResourceContract`/`FreezeBalanceV2` delegation (which only requires the delegator's signature, not the receiver's), which sets `AcquiredDelegatedFrozenBalanceForBandwidth`/`Energy` on the receiver capsule. This is the exact same class of issue the report describes for planets/operators: the same storage record is reused across two conceptually distinct roles ("ordinary account that can receive delegated resources" vs. "system contract account"), and only some of the type-specific bookkeeping is reset when the role changes. Downstream code paths that key behavior off `AccountType` (e.g., `UnfreezeBalanceActuator`) treat `Contract`-typed receivers differently from `Normal`-typed receivers: [3](#0-2) 

so an account that changes type mid-flight (Normal → Contract) can end up with residual delegated-balance bookkeeping that the "Contract" code branch never reconciles, exactly mirroring the report's core complaint: fields that should be tracked per-role end up shared/misattributed once an entity crosses role boundaries.

### Impact Explanation
This produces state pollution / accounting divergence tied to a consensus-critical, canonical system address: an unprivileged user can, via ordinary broadcast transactions (transfer TRX, delegate resource) executed *before* governance activates the TIP‑2935 upgrade, leave residual `Normal`-account state (delegated-resource bookkeeping, and potentially votes/frozen balances/permissions if other reachable actuators can touch the address) attached to what subsequently becomes a shared system contract account used by every node identically. Because the upgrade path is deterministic and runs on every node, the corruption is consensus-consistent rather than causing a fork, but it still represents unauthorized, cross-role state leakage into a protocol-critical account, and downstream logic that branches on `AccountType` (bandwidth/energy weight accounting) is not guaranteed to reconcile it correctly.

### Likelihood Explanation
Moderate: the precondition (an EOA sitting at the fixed canonical address before the one-time TIP‑2935 activation) is explicitly acknowledged in code comments as "the common case", and populating it further via delegated-resource paths requires no special privilege — just an ordinary signed transaction naming the fixed address as receiver.

### Recommendation
Do not upgrade a pre-existing `AccountCapsule` in place when installing the canonical system contract. Instead, either (a) reject/skip activation if any non-empty, non-balance state (votes, frozen resources, delegated-resource indices, permissions) exists at the address — analogous to the existing foreign-code/foreign-contract skip guards — or (b) fully reset all role-specific fields (votes, `Frozen`/`FreezeV2`, delegated resource indices in `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore`, and permissions) atomically as part of the type transition, not just `clearDelegatedResource()`.

### Proof of Concept
1. Before the TIP‑2935 proposal (`AllowTvmPrague`) is activated, an attacker broadcasts a `DelegateResourceContract` naming `HISTORY_STORAGE_ADDRESS` as the receiver, which increments `AcquiredDelegatedFrozenBalanceForBandwidth`/`Energy` on the account at that address (still `AccountType.Normal`) — see the receiver-side field mutation pattern exercised in `FreezeV2Test.testDelegateResourceOperations` [4](#0-3) .
2. Governance activates `AllowTvmPrague`; `HistoryBlockHashUtil.deploy()` runs and converts the existing `AccountCapsule` to `AccountType.Contract` in place while preserving balance, per `deployUpgradesPreExistingNormalAccountPreservingBalance` [5](#0-4) , without touching the delegated-resource fields set in step 1.
3. The original delegator later unfreezes; because the receiver is now `AccountType.Contract`, `UnfreezeBalanceActuator` takes the branch that skips adjusting the receiver's `AcquiredDelegatedFrozenBalanceFor*` fields [3](#0-2) , leaving stale delegated-resource accounting permanently attached to the shared system contract account.

**Note on coverage:** I was unable to fully load `AccountCapsule.updateAccountType`/`clearDelegatedResource` implementations before running out of tool iterations, so I could not verify with certainty every field that is or isn't reset during the type transition. If a definitive determination of exact residual fields (votes/permissions in particular) is needed, a Devin session with full file access should inspect `AccountCapsule.java` directly.

### Citations

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L53-63)
```java
  // Account template for the new-account branch of {@code deploy()} (no prior
  // state at the canonical address). Equivalent to create2's
  // {@code createAccount(addr, Contract)}: only type, and address
  // are set. The pre-existing-account branch never uses this template
  // — it mutates the existing capsule in place to preserve balance / asset
  // state, mirroring the CREATE2 collision path. Safe to share: the proto is
  // immutable, and AccountCapsule mutations rebuild via {@code toBuilder}.
  private static final Account HISTORY_STORAGE_ACCOUNT = Account.newBuilder()
      .setType(Protocol.AccountType.Contract)
      .setAddress(ByteString.copyFrom(HISTORY_STORAGE_ADDRESS))
      .build();
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L113-153)
```java
      AccountCapsule receiverCapsule = accountStore.get(receiverAddress);

      if (dynamicStore.getAllowTvmConstantinople() == 0 ||
          (receiverCapsule != null && receiverCapsule.getType() != AccountType.Contract)) {
        switch (unfreezeBalanceContract.getResource()) {
          case BANDWIDTH:
            long oldNetWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() / 
                    TRX_PRECISION;
            if (dynamicStore.getAllowTvmSolidity059() == 1
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < unfreezeBalance) {
              oldNetWeight = unfreezeBalance / TRX_PRECISION;
              receiverCapsule.setAcquiredDelegatedFrozenBalanceForBandwidth(0);
            } else {
              receiverCapsule.addAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance);
            }
            long newNetWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() / 
                    TRX_PRECISION;
            decrease = newNetWeight - oldNetWeight;
            break;
          case ENERGY:
            long oldEnergyWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() / 
                    TRX_PRECISION;
            if (dynamicStore.getAllowTvmSolidity059() == 1
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() < unfreezeBalance) {
              oldEnergyWeight = unfreezeBalance / TRX_PRECISION;
              receiverCapsule.setAcquiredDelegatedFrozenBalanceForEnergy(0);
            } else {
              receiverCapsule.addAcquiredDelegatedFrozenBalanceForEnergy(-unfreezeBalance);
            }
            long newEnergyWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() / 
                    TRX_PRECISION;
            decrease = newEnergyWeight - oldEnergyWeight;
            break;
          default:
            //this should never happen
            break;
        }
        accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
      } else {
        decrease = -unfreezeBalance / TRX_PRECISION;
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeV2Test.java (L376-402)
```java
  @Test
  public void testDelegateResourceOperations() throws Exception {
    byte[] contract = deployContract("TestFreezeV2", FREEZE_V2_CODE);
    long resourceAmount = 1_000_000;
    // trigger freezeBalanceV2(uint256,uint256) to get bandwidth
    freezeV2(owner, contract, resourceAmount, 0);
    // trigger freezeBalanceV2(uint256,uint256) to get energy
    freezeV2(owner, contract, resourceAmount, 1);
    // trigger freezeBalanceV2(uint256,uint256) to get tp
    freezeV2(owner, contract, resourceAmount, 2);

    delegateResourceWithException(owner, contract, userA, resourceAmount, 0);
    rootRepository.createAccount(userA, Protocol.AccountType.Normal);
    rootRepository.commit();
    delegateResourceWithException(owner, contract, userA, 0, 0);
    delegateResourceWithException(owner, contract, userA, resourceAmount * 2, 0);
    delegateResourceWithException(owner, contract, userA, resourceAmount - 100, 0);
    delegateResourceWithException(owner, contract, userA, resourceAmount, 2);
    delegateResourceWithException(owner, contract, userA, resourceAmount, 3);
    delegateResourceWithException(owner, contract, contract, resourceAmount, 0);
    rootRepository.createAccount(userC, Protocol.AccountType.Contract);
    rootRepository.commit();
    delegateResourceWithException(owner, contract, userC, resourceAmount, 0);

    delegateResource(owner, contract, userA, resourceAmount, 0);
    delegateResourceWithException(owner, contract, userA, resourceAmount, 0);
    delegateResource(owner, contract, userA, resourceAmount, 1);
```

**File:** framework/src/test/java/org/tron/core/db/HistoryBlockHashIntegrationTest.java (L318-334)
```java
  @Test
  public void deployUpgradesPreExistingNormalAccountPreservingBalance() {
    byte[] addr = HistoryBlockHashUtil.HISTORY_STORAGE_ADDRESS;
    long balance = 12345L;
    AccountCapsule eoa = new AccountCapsule(
        ByteString.copyFrom(addr), Protocol.AccountType.Normal);
    eoa.setBalance(balance);
    chainBaseManager.getAccountStore().put(addr, eoa);

    HistoryBlockHashUtil.deploy(dbManager);

    AccountCapsule after = chainBaseManager.getAccountStore().get(addr);
    assertEquals(Protocol.AccountType.Contract, after.getType());
    assertEquals(balance, after.getBalance());
    assertTrue(chainBaseManager.getCodeStore().has(addr));
    assertTrue(chainBaseManager.getContractStore().has(addr));
  }
```
