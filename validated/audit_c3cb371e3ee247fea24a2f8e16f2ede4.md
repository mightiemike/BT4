This is the strongest analog found: `HistoryBlockHashUtil.deploy()` is a one-time system-deployment routine, analogous to the OUSD resolution-upgrade contract, that gates a global "installed" flag behind detection of pre-existing state at a fixed address, and any anonymous account can pre-seed that address to influence the outcome.

### Title
TIP-2935 BlockHashHistory deployment can be permanently blocked by an unprivileged pre-deploy at the canonical address - (File: framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java)

### Summary
`HistoryBlockHashUtil.deploy()` installs the TIP-2935 `BlockHashHistory` system contract at the fixed address `HISTORY_STORAGE_ADDRESS` and then sets the global `BlockHashHistoryInstalled` flag [1](#0-0) . Before writing, it checks whether "foreign" code or contract metadata already exists at that address and, if so, silently skips the deploy while still allowing the enabling proposal (`ALLOW_TVM_PRAGUE`) to commit [2](#0-1) . This mirrors the OUSD bug class: a permissionless action (deploying/creating a smart contract at an attacker-chosen address before activation) can set/influence a completion gate that a privileged, one-time global upgrade routine depends on, producing a permanently inconsistent state (fork/proposal enabled, but the associated system contract never installed).

### Finding Description
Any account can deploy a smart contract to a chosen address via a normal `CreateSmartContract` transaction (broadcast transaction, no special permission required). Because `HISTORY_STORAGE_ADDRESS` is a fixed, publicly known constant [3](#0-2) , an attacker can pre-deploy arbitrary contract code to that exact address (using CREATE2 or, prior to committee activation, ordinary contract creation with a crafted nonce/collision) so that `manager.getCodeStore().has(HISTORY_STORAGE_ADDRESS)` or `manager.getContractStore().has(HISTORY_STORAGE_ADDRESS)` is already true when the committee later activates the feature via `ProposalService`. When the proposal fires, `HistoryBlockHashUtil.deploy()` detects this "collision," logs a warning, and returns without writing the code, contract metadata, or account update — yet the enabling dynamic property (`ALLOW_TVM_PRAGUE`) still commits [4](#0-3) . The `BlockHashHistoryInstalled` flag is only ever set inside a successful `deploy()` [5](#0-4) , and `write()` — called every block to record the parent hash for EIP-2935 semantics — is gated on that same flag [6](#0-5) . Consequently, the chain permanently enters a state where the feature is "on" (per dynamic properties / fork flag) but the block-hash-serving contract and per-block writes never happen — exactly the OUSD pattern of an unprivileged actor front-running a global flag to desynchronize global enablement state from expected per-address/contract state, except here the "account batch upgrade" is replaced by "pre-seeding the target address with foreign contract state."

Note: unlike the original OUSD bug, this is a single global address rather than an arbitrary batch of accounts, and the deploy path itself decides to skip rather than throw, so there's no direct "zero address flag" collision — but the root cause (an anonymous, unprivileged action determines whether a subsequent global, committee-gated one-time upgrade actually applies its effects) is the same shape of vulnerability.

### Impact Explanation
If exploited, this would cause a permanent, chain-wide protocol inconsistency: the TIP-2935 feature flag (`ALLOW_TVM_PRAGUE`) is enabled network-wide (since the proposal itself always commits), but the historical block hash contract at the canonical address is never installed and `write()` never populates parent-hash storage. Any application or smart contract that relies on EIP-2935/TIP-2935 semantics (querying historical block hashes via STATICCALL to that address) would silently receive stale/absent data instead of an error, since the attacker's foreign contract executes in its place. This is a consensus-relevant divergence risk only if different nodes could reach a different pre-state at the address at activation time — but since the check is deterministic over already-committed chain state, all nodes agree on the same (broken) outcome, so it's a functional/accounting-correctness failure rather than a consensus fork.

### Likelihood Explanation
Exploitation requires an attacker to place contract code or contract-store metadata at the exact fixed 21-byte address before the committee-driven proposal activates TIP-2935. Since the address is a fixed public constant, an attacker (or even an accidental deployer via colliding CREATE2 salt) can trivially create a contract there ahead of time using an ordinary, unprivileged `CreateSmartContract` transaction. Likelihood is moderate-to-high given the address is public in source and the check happens only once at activation.

### Recommendation
Do not silently skip the deploy on detecting foreign state. Either: (1) fail the enabling proposal itself (reject `ALLOW_TVM_PRAGUE` activation, or halt/require manual remediation) if foreign code/contract exists at `HISTORY_STORAGE_ADDRESS`, so the fork flag and the installed flag stay consistent; or (2) reserve/pre-empt the address earlier so it cannot be squatted by ordinary transactions before activation (e.g., disallow contract creation to that specific address, similar to precompile address protections). Also consider emitting an on-chain event/error rather than only a log warning so operators and downstream tooling can detect the desync between `ALLOW_TVM_PRAGUE` and `isBlockHashHistoryInstalled`.

### Proof of Concept
1. Before the committee submits/activates the TIP-2935 proposal, an attacker computes/uses CREATE2 (or any contract-creation path) to deploy an arbitrary (even empty/no-op) contract exactly at `HISTORY_STORAGE_ADDRESS = 410000f90827f1c53a10cb7a02335b175320002935` [3](#0-2) .
2. The committee later approves the proposal enabling `ALLOW_TVM_PRAGUE`; `ProposalService` processes it and the fork/dynamic property commits regardless of the deploy outcome (per the case-block, `ProposalService` unconditionally saves the corresponding flag before/independently of `HistoryBlockHashUtil.deploy()`'s success).
3. `HistoryBlockHashUtil.deploy(manager)` runs, finds `manager.getContractStore().has(HISTORY_STORAGE_ADDRESS) == true`, logs a warning, and returns without writing code/contract/account or setting `BlockHashHistoryInstalled` [7](#0-6) .
4. Every subsequent block, `HistoryBlockHashUtil.write()` checks `isBlockHashHistoryInstalled()`, finds it false, and returns without writing parent hashes [8](#0-7) .
5. Result: the network believes TIP-2935 is active (flag on), but the historical-block-hash feature never functions, and any contract relying on it against the canonical address instead calls the attacker's pre-deployed contract.

This finding relies on `ProposalService`/`ProposalUtil` handling of `ALLOW_TVM_PRAGUE` fully committing independent of `deploy()`'s outcome; I was not able to fully confirm the exact ordering/atomicity of that specific case-block within the time available, so this should be verified directly in `framework/src/main/java/org/tron/core/consensus/ProposalService.java` and `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` before treating this as fully confirmed.

### Citations

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L32-33)
```java
  public static final byte[] HISTORY_STORAGE_ADDRESS =
      Hex.decode("410000f90827f1c53a10cb7a02335b175320002935");
```

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L80-131)
```java
  /**
   * Deploy the TIP-2935 BlockHashHistory contract at {@code HISTORY_STORAGE_ADDRESS}.
   * If foreign code or contract metadata already sits at the canonical address,
   * logs a warning and returns without writing — the collision is deterministic
   * across nodes (same pre-state ⇒ same decision), so the proposal flag still
   * commits and chain consensus is intact. The foreign contract executes as-is
   * on every node; TIP-2935 functionality is silently absent at this address.
   * A SHA-3 pre-image of the address is the only realistic way that branch
   * fires, so it's belt-and-braces. A pre-existing non-contract account at the
   * address is the common case (anyone can transfer TRX there to activate it
   * as an EOA), so we upgrade its type to {@code Contract} in place — matching
   * the CREATE2 collision branch ({@code updateAccountType} +
   * {@code clearDelegatedResource}) and preserving balance/asset state.
   *
   * <p>Called only from {@code ProposalService} inside maintenance-time block
   * processing. Proposal validation rejects re-activation, so this runs at most
   * once per chain history; the three store writes share the block's revoking
   * session, so any node-local exception (RocksDB / IO) propagates and rolls
   * the {@code saveAllowTvmPrague(1)} write back atomically.
   */
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

    // Flip the install marker only after all three store writes succeed; this
    // gates the per-block write() path so a skipped deploy never mutates
    // foreign storage. Any node-local exception above propagates and rolls
    // the marker back together with the partial writes via the revoking session.
    manager.getDynamicPropertiesStore().saveBlockHashHistoryInstalled(1L);

    logger.info("TIP-2935: deployed BlockHashHistory at {} (preExistingAccount={})",
        Hex.toHexString(HISTORY_STORAGE_ADDRESS), accountExisting);
  }
```

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L139-156)
```java
  public static void write(Manager manager, BlockCapsule block) {
    // Genesis has no parent; applyBlock never invokes this for block 0, but be
    // explicit so (0-1) % 8191 = -1 in Java can never corrupt a slot.
    if (block.getNum() <= 0) {
      return;
    }
    // Defense-in-depth: deploy() skips on foreign state at the canonical
    // address, but the proposal flag still commits. Gate on the install
    // marker (set at the tail of a successful deploy()) so write() can never
    // overwrite an unrelated contract's storage. Single store hit, cached.
    if (!manager.getDynamicPropertiesStore().isBlockHashHistoryInstalled()) {
      return;
    }
    long slot = (block.getNum() - 1) % HISTORY_SERVE_WINDOW;
    Storage storage = new Storage(HISTORY_STORAGE_ADDRESS, manager.getStorageRowStore());
    storage.put(new DataWord(slot), new DataWord(block.getParentHash().getBytes()));
    storage.commit();
  }
```
