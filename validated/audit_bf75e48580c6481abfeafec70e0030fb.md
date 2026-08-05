## Title
Assets and account state sent to a precomputed CREATE2 address before contract deployment persist into the deployed contract, breaking assumptions of a "clean" initial state - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
The Notional report describes an attacker sending fCash of the wrong maturity to a `CREATE2` address before the wrapper contract is deployed there; because nothing is deployed yet, the ERC1155 receive hook never fires, the transfer silently succeeds, and once the real contract is deployed it inherits an unexpected asset balance that permanently breaks its invariants. The java-tron analog is `Program.createContractImpl`, which handles the "account already exists at the target address" branch by upgrading the existing account in place rather than requiring a clean/empty account, so any TRX, TRC10 (`AssetV2`), votes, or resource state deposited at a *precomputable* `CREATE2` address before deployment survives into the newly-deployed contract.

### Finding Description
`CREATE2` addresses in TVM are fully deterministic and can be computed off-chain by any unprivileged user using the documented formula `keccak256(prefix ++ sender ++ salt ++ keccak256(code))[12:]`, as demonstrated in the test helper itself: [1](#0-0) .

Because this address can be known before the factory ever calls `CREATE2`, anyone can transfer TRX, issue/participate a TRC10 asset to it, freeze/delegate resources to it, or vote from it while it is still an ordinary externally-activatable account.

When the real deployment eventually happens, `Program.createContractImpl` looks up the existing account and, if one is found, does **not** wipe it — it only flips the account type and clears delegated-resource fields, preserving everything else (balance, TRC10 `AssetV2` map, votes, etc.): [2](#0-1) 

The asset-preserving accessors confirm that TRC10 balances placed on the account before deployment (`addAssetV2`/`getAssetV2`) are ordinary mutable map entries on the capsule and are not reset by the CREATE2 path: [3](#0-2) .

The project's own tests confirm and rely on this "pre-fund before CREATE2 deploy" pattern for TRX/delegated resources, showing the address is usable (and stateful) prior to deployment: [4](#0-3) , and the same pattern is explicitly called out as the "CREATE2 collision path" that the newer `HistoryBlockHashUtil.deploy` code intentionally mirrors: [5](#0-4) .

### Impact Explanation
A contract deployed via `CREATE2` can be forced to start life holding attacker-supplied TRC10 token balances, TRX balance, or historical vote/resource state it never expected. Any downstream contract logic that assumes a freshly deployed contract begins with zero token/asset balance (e.g., a token wrapper, vault, or accounting contract that checks `balanceOf(address(this)) == 0` or enumerates its own asset holdings before minting/crediting shares) can be permanently corrupted or bricked once real funds/assets are added post-deployment — an invalid-state/divergence between the contract's assumed and actual initial accounting, mirroring the original report's "mint permanently broken" outcome. This is a loss-of-availability/invalid-state class issue rather than a direct fund-theft bug, consistent with the Medium severity assigned to the original finding.

### Likelihood Explanation
Exploitability is fully unprivileged: any user can compute the target `CREATE2` address off-chain using the public, documented formula and then send TRX or participate in/transfer a TRC10 asset to it before the deploying contract calls `CREATE2`. No special permissions, timing races beyond "before deployment," or trusted roles are required, matching the "only possible before contract deployment" caveat from the original judged report.

### Recommendation
When taking the "account already exists" branch in `createContractImpl` (and the analogous `HistoryBlockHashUtil.deploy` pre-existing-account branch), consider exposing a way for the newly deployed contract (or its deployer) to detect and clear/reject unexpected pre-existing TRC10/vote/resource state, or document this behavior clearly so application-level contracts relying on CREATE2 do not assume a zero-value/asset initial state. At minimum, this behavior should be explicitly documented for TVM contract developers so wrapper/vault-style contracts add their own "sweep unexpected assets on init" guard, analogous to the fix recommended in the original report.

### Proof of Concept
1. Deploy a factory contract `F`.
2. Off-chain, compute `addr = keccak256(0x41 ++ F ++ salt ++ keccak256(initcode))[12:]` per the formula in `FreezeTest.sol` (`getCreate2Addr`).
3. Before `F` calls `create2(...)` with that `salt`/`initcode`, as an unprivileged user: transfer TRX to `addr` and/or issue+participate a TRC10 asset crediting `addr` (standard `AssetIssueActuator`/`ParticipateAssetIssueActuator` flows), and/or delegate resources to it (as done in `testFreezeAndUnfreezeToCreate2Contract`).
4. Trigger `F.deploy(initcode, salt)`, which internally calls `TVM CREATE2` → `Program.createContractImpl`.
5. Observe that `deposit.getAccount(newAddress)` is non-null, `contractAlreadyExists` is `false` (no code yet), so the existing-account branch runs: only `updateAccountType`/`clearDelegatedResource` execute, while balance and `AssetV2` map are carried over unchanged, per lines 827-843 of `Program.java`.
6. The deployed contract now holds the pre-planted balance/assets, which any contract logic assuming a clean initial state cannot detect or reject after the fact.

### Citations

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.sol (L65-86)
```text
    // selector: 0xbb63e785
    // Predict CREATE2 address without deploying
    //
    // TRON CREATE2 formula (differs from standard EVM):
    //   address = keccak256(prefix ++ sender[20] ++ salt[32] ++ keccak256(code)[32])[12:]
    //
    // - Standard EVM uses 0xff as prefix (magic byte)
    // - TRON replaces it with the address prefix byte (0x41 for mainnet, 0xa0 for testnet)
    // - This value is hardcoded at compile time by tron-solc
    //
    function getCreate2Addr(uint256 salt) public view returns (address) {
        bytes memory bytecode = type(FreezeContract).creationCode;
        bytes32 hash = keccak256(
            abi.encodePacked(
                bytes1(0x41),       // TRON mainnet address prefix
                address(this),      // 20-byte factory address
                salt,               // 32-byte salt
                keccak256(bytecode) // 32-byte code hash
            )
        );
        return address(uint160(uint256(hash)));
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-843)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
    Repository deposit = getContractState().newRepositoryChild();
    if (VMConfig.allowTvmConstantinople()) {
      if (existingAccount == null) {
        deposit.createAccount(newAddress, "CreatedByContract",
            AccountType.Contract);
      } else if (!contractAlreadyExists) {
        existingAccount.updateAccountType(AccountType.Contract);
        existingAccount.clearDelegatedResource();
        deposit.updateAccount(newAddress, existingAccount);
      }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L838-868)
```java
  public boolean addAssetV2(byte[] key, long value) {
    if (AssetUtil.hasAssetV2(this.account, key)) {
      return false;
    }

    this.account = this.account.toBuilder()
        .putAssetV2(ByteArray.toStr(key), value)
        .build();
    return true;
  }

  public void addAssetMapV2(Map<String, Long> assetMap) {
    this.account = this.account.toBuilder().putAllAssetV2(assetMap).build();
  }

  public Long getAsset(DynamicPropertiesStore dynamicStore, String key) {
    Long balance;
    if (dynamicStore.getAllowSameTokenName() == 0) {
      balance = this.account.getAssetMap().get(key);
    } else {
      importAsset(key.getBytes());
      balance = this.account.getAssetV2Map().get(key);
    }
    return balance;
  }

  public long getAssetV2(String key) {
    importAsset(key.getBytes());
    Long balance = this.account.getAssetV2Map().get(key);
    return balance == null ? 0 : balance;
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java (L372-391)
```java
  @Test
  public void testFreezeAndUnfreezeToCreate2Contract() throws Exception {
    byte[] factoryAddr = deployContract("FactoryContract", FACTORY_CODE);
    byte[] contractAddr = deployContract("TestFreeze", CONTRACT_CODE);
    long frozenBalance = 1_000_000;
    long salt = 1;
    byte[] predictedAddr = getCreate2Addr(factoryAddr, salt);
    Assert.assertNull(dbManager.getAccountStore().get(predictedAddr));
    freezeForOther(contractAddr, predictedAddr, frozenBalance, 0);
    Assert.assertNotNull(dbManager.getAccountStore().get(predictedAddr));
    freezeForOther(contractAddr, predictedAddr, frozenBalance, 1);
    unfreezeForOtherWithException(contractAddr, predictedAddr, 0);
    unfreezeForOtherWithException(contractAddr, predictedAddr, 1);
    clearDelegatedExpireTime(contractAddr, predictedAddr);
    unfreezeForOther(contractAddr, predictedAddr, 0);
    unfreezeForOther(contractAddr, predictedAddr, 1);

    freezeForOther(contractAddr, predictedAddr, frozenBalance, 0);
    freezeForOther(contractAddr, predictedAddr, frozenBalance, 1);
    Assert.assertArrayEquals(predictedAddr, deployCreate2Contract(factoryAddr, salt));
```

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L53-60)
```java
  // Account template for the new-account branch of {@code deploy()} (no prior
  // state at the canonical address). Equivalent to create2's
  // {@code createAccount(addr, Contract)}: only type, and address
  // are set. The pre-existing-account branch never uses this template
  // — it mutates the existing capsule in place to preserve balance / asset
  // state, mirroring the CREATE2 collision path. Safe to share: the proto is
  // immutable, and AccountCapsule mutations rebuild via {@code toBuilder}.
  private static final Account HISTORY_STORAGE_ACCOUNT = Account.newBuilder()
```
