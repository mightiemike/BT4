### Title
DoS: Attacker May Front-Run `AssetIssueContract` (`createAssetIssue`) With A Duplicate Token `name` Causing The Victim's Transaction To Revert With "Token exists" - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
When `AllowSameTokenName` is disabled (`getAllowSameTokenName() == 0`), `AssetIssueActuator.validate()` treats the human-readable token `name` field as a globally unique key, rejecting any transaction whose `name` already exists in `AssetIssueStore`. Because this uniqueness check happens only at block-processing time (not reserved atomically when the transaction is broadcast), an attacker who observes a pending `createAssetIssue` transaction in the mempool can front-run it with their own `createAssetIssue` transaction using the identical `name`. If the attacker's transaction is packed into a block first, the original transaction becomes invalid ("Token exists") when it is later processed, exactly mirroring the `SplitFactory.createSplit()` `merkleRoot`-as-`salt` front-running bug: a value the victim chose deterministically (their desired token name) is consumed by an attacker before the victim's transaction lands, permanently blocking the victim from using that name.

### Finding Description
`AssetIssueActuator.validate()` performs this check: [1](#0-0) 

```java
if (dynamicStore.getAllowSameTokenName() == 0
    && assetIssueStore.get(assetIssueContract.getName().toByteArray())
    != null) {
  throw new ContractValidateException("Token exists");
}
```

The `name` is user-supplied, chosen independently by the account issuing the asset, and is used as the primary key for `AssetIssueStore` (`assetIssueCapsule.createDbKey()`), just as `merkleRoot` was used as the CREATE2 `salt` in the referenced Solidity bug. There is no binding of the name to `msg.sender`/owner address before the transaction executes — any address can claim any unused name, first-come-first-served at execution time rather than at submission time.

This creates the same race condition class as the external report:
1. Victim broadcasts `createAssetIssue` with `name = "FOO"`.
2. Attacker observes this transaction in the mempool.
3. Attacker crafts and broadcasts their own `createAssetIssue` with the same `name = "FOO"` (different owner/parameters), using a higher bandwidth/energy fee or priority to get included in an earlier block or earlier position in the same block.
4. `AssetIssueActuator.execute()` for the attacker's transaction succeeds and stores the asset under key `"FOO"`.
5. When the victim's transaction is subsequently processed, `validate()` finds `assetIssueStore.get("FOO") != null` and throws `ContractValidateException("Token exists")`, permanently invalidating the victim's transaction for that name (repeatable for any name the attacker chooses to squat).

This is enforced only under the legacy `AllowSameTokenName == 0` mode; when the parameter is enabled (`== 1`, current mainnet default), asset identifiers are auto-generated numeric token IDs rather than user-chosen names, so this specific instance is only reachable on chains/private networks that still run with `AllowSameTokenName` disabled. It is nonetheless present in the production actuator code and is a faithful structural analog of the `SplitFactory` bug: an attacker permanently consumes a caller-chosen unique identifier ahead of the legitimate owner, causing predictable, repeatable transaction failure/DoS.

### Impact Explanation
- The victim's `createAssetIssue` transaction reverts on-chain with `ContractValidateException`, meaning the transaction is rejected during validation (no state change, but the user wastes bandwidth/energy resources and time attempting to issue their token).
- The attack is trivially repeatable: the victim must choose a new name and can be front-run again, mirroring the report's conclusion that "there is no guarantee this new merkle root will be successfully added ... without the attacker front-running the transaction again."
- Because `AssetIssueContract.name` is also checked against `AccountCapsule.getAssetIssuedName()` per-account (each account may issue only one asset), a targeted victim can be denied a specific brand/ticker name they intended to use for their token launch, which has reputational/business impact (name squatting/griefing), analogous to the DoS described in the report.

### Likelihood Explanation
Exploitation requires only observing pending transactions in the public mempool and submitting a competing transaction with higher priority (fee/energy), which is a standard, low-cost front-running technique available to any unprivileged network participant — no special permissions are needed. The precondition is that the network/chain has `AllowSameTokenName == 0` (the original v1 token-name uniqueness mode); this reduces general applicability to mainnet today (where `AllowSameTokenName` has long been enabled), but the vulnerable code path remains live in the actuator and would be fully exploitable on any deployment (e.g., private/sidechain deployments of this java-tron fork) that has not enabled `AllowSameTokenName`.

### Recommendation
- If token-name uniqueness must be preserved under legacy mode, bind the reservation to the submitting account at the time uniqueness is checked/reserved rather than relying purely on a race won at block-inclusion time — e.g., require a commit-reveal scheme (commit `hash(owner || name)` first, then reveal), or scope name uniqueness per-owner instead of globally.
- Alternatively, and more consistent with the current codebase direction, ensure `AllowSameTokenName` remains permanently enabled at the protocol level so all new token identifiers are the auto-incrementing numeric `tokenIdNum` rather than an attacker-guessable/front-runnable free-text `name`, removing the race entirely (as already done for `AllowSameTokenName == 1`).

### Proof of Concept
1. Deploy/configure a java-tron network with `saveAllowSameTokenName(0)`.
2. Account A broadcasts `CreateSmartContract`-equivalent `AssetIssueContract` transaction with `name = "MYTOKEN"`.
3. Attacker, monitoring the mempool, broadcasts their own `AssetIssueContract` transaction with `name = "MYTOKEN"` and a higher energy/bandwidth fee so it is prioritized into an earlier block position.
4. Attacker's transaction executes first via `AssetIssueActuator.execute()`, storing the asset under key `"MYTOKEN"`: [2](#0-1) 
5. Account A's transaction is then validated and fails: [1](#0-0) 
This is directly reproduced by the existing test `IssueSameTokenNameAssert` in `AssetIssueActuatorTest.java`, which demonstrates that a duplicate `name` causes `ContractValidateException("Token exists")` when `AllowSameTokenName == 0`: [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L78-87)
```java
      if (dynamicStore.getAllowSameTokenName() == 0) {
        assetIssueCapsuleV2.setPrecision(0);
        assetIssueStore
            .put(assetIssueCapsule.createDbKey(), assetIssueCapsule);
        assetIssueV2Store
            .put(assetIssueCapsuleV2.createDbV2Key(), assetIssueCapsuleV2);
      } else {
        assetIssueV2Store
            .put(assetIssueCapsuleV2.createDbV2Key(), assetIssueCapsuleV2);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L210-214)
```java
    if (dynamicStore.getAllowSameTokenName() == 0
        && assetIssueStore.get(assetIssueContract.getName().toByteArray())
        != null) {
      throw new ContractValidateException("Token exists");
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java (L1700-1731)
```java
  @Test
  public void IssueSameTokenNameAssert() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);
    String ownerAddress = "418beaa1a8e2d45367af7bae7c49009876a4fa4301";

    long id = dbManager.getDynamicPropertiesStore().getTokenIdNum() + 1;
    dbManager.getDynamicPropertiesStore().saveTokenIdNum(id);
    AssetIssueContract assetIssueContract = AssetIssueContract.newBuilder()
        .setOwnerAddress(ByteString.copyFrom(ByteArray.fromHexString(ownerAddress)))
        .setName(ByteString.copyFrom(ByteArray.fromString(NAME))).setId(Long.toString(id))
        .setTotalSupply(TOTAL_SUPPLY)
        .setTrxNum(TRX_NUM).setNum(NUM).setStartTime(1).setEndTime(100).setVoteScore(2)
        .setDescription(ByteString.copyFrom(ByteArray.fromString(DESCRIPTION)))
        .setUrl(ByteString.copyFrom(ByteArray.fromString(URL))).build();
    AssetIssueCapsule assetIssueCapsule = new AssetIssueCapsule(assetIssueContract);
    dbManager.getAssetIssueStore().put(assetIssueCapsule.createDbKey(), assetIssueCapsule);

    AccountCapsule ownerCapsule = new AccountCapsule(
        ByteString.copyFrom(ByteArray.fromHexString(ownerAddress)),
        ByteString.copyFromUtf8("owner11"), AccountType.AssetIssue);
    ownerCapsule.addAsset(NAME.getBytes(), 1000L);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    AssetIssueActuator actuator = new AssetIssueActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract());

    TransactionResultCapsule ret = new TransactionResultCapsule();
    Long blackholeBalance = dbManager.getAccountStore().getBlackhole().getBalance();
    // SameTokenName not active, same assert name, should failure

    processAndCheckInvalid(actuator, ret, "Token exists", "Token exists");

```
