### Title
Front-runnable global asset-name uniqueness check enables censorship of `AssetIssueContract` transactions - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
`AssetIssueActuator.validate()` treats the raw asset `name` string as the sole identifier of uniqueness when `AllowSameTokenName` is disabled, without binding that check to the address of the transaction's owner/initiator. An attacker who observes a pending `AssetIssueContract` broadcast in the mempool can copy the desired `name` field into their own transaction, get it confirmed first, and cause the original user's transaction to permanently fail with "Token exists" — an on-chain analog of the Meson `encodedSwap` front-running DoS, where a content-derived identifier lacking a binding to the legitimate initiator allows a griefer to pre-empt the state slot.

### Finding Description
In `validate()`, the only defense against duplicate token issuance is: [1](#0-0) 

```java
if (dynamicStore.getAllowSameTokenName() == 0
    && assetIssueStore.get(assetIssueContract.getName().toByteArray())
    != null) {
  throw new ContractValidateException("Token exists");
}
```

The lookup key is `assetIssueContract.getName()` — pure user-supplied content — with no cryptographic or address-based binding to the account that originally intended to issue that token. This is structurally identical to MesonSwap's `_postedSwaps[encodedSwap]` pattern flagged in the report: the primary uniqueness key is derived only from swap/asset content, not from the submitter's identity, so whoever's transaction lands first in a block "wins" the slot regardless of who actually crafted it first.

Execution then persists the winner's data keyed by that same name: [2](#0-1) 

Because `name` is visible in any broadcast (mempool-visible) `AssetIssueContract` transaction before it's packed into a block, a bad actor (or MEV-style relayer) can extract the `name` field, construct their own `AssetIssueContract` with the same `name` (and possibly a small bribe/fee/priority advantage), and get it validated/executed by a block producer first. The original user's transaction subsequently fails validation with "Token exists", exactly mirroring the "Swap already exists" DoS in the Meson report.

### Impact Explanation
This allows a griefer to selectively censor any specific user's asset-issuance transaction by squatting the exact token name they intend to use, denying them the ability to ever issue that specific asset name (a scarce, first-come-first-served resource on chain, similar to how MesonSwap's `encodedSwap` slot is a scarce, first-come resource per swap). This is a targeted denial-of-service against the honest actuator state transition for `AssetIssueContract`, reachable directly by any anonymous account able to broadcast a transaction via RPC.

### Likelihood Explanation
Exploitability requires (1) `AllowSameTokenName` dynamic parameter to be `0` (this is a network-configurable chain parameter, historically the default on some java-tron-based networks before broad adoption of `AllowSameTokenName=1`), and (2) the attacker being able to observe the victim's pending transaction (mempool visibility) and get a competing transaction included first — both are realistic for any public network with a public mempool/broadcast API. Likelihood is Low-to-Medium: it depends on the specific chain's `AllowSameTokenName` setting, and unlike MesonSwap there's no direct financial extraction for the attacker, only griefing value — matching the report's original assessment that "front-running is unlikely to be profitable" but can still "dramatically affect a specific user's ability to transact."

### Recommendation
- Short term: monitor networks where `AllowSameTokenName == 0` for `AssetIssueContract` failures with reason "Token exists" that correlate with mempool-visible identical `name` fields from different owner addresses, indicating front-running/censorship.
- Long term: bind asset-name uniqueness reservations to the intended owner (e.g., allow an owner to "reserve" or commit-reveal the name, or key uniqueness on `(name, ownerAddress)` with a subsequent global rename/claim step), so that duplicate broadcasts by third parties cannot pre-empt the legitimate initiator's transaction. Alternatively, deprecate/disable the `AllowSameTokenName == 0` mode entirely in favor of the tokenId-based (`AssetIssueV2Store`) scheme already used when `AllowSameTokenName != 0`, since that path does not have this global name-collision race.

### Proof of Concept
1. Network has `AllowSameTokenName` set to `0` (checked via `DynamicPropertiesStore.getAllowSameTokenName()`).
2. Victim broadcasts `AssetIssueContract` with `name = "MYTOKEN"` from address `A`.
3. Attacker observes this transaction in the mempool/broadcast layer, extracts `name = "MYTOKEN"`, and immediately broadcasts their own `AssetIssueContract` with the identical `name` from address `B`, optionally with higher fee/priority to get block inclusion first.
4. Block producer processes attacker's transaction first: `AssetIssueActuator.execute()` stores the asset under key `"MYTOKEN"` in `AssetIssueStore` (see `assetIssueCapsule.createDbKey()` write at [3](#0-2) ).
5. Victim's transaction is then validated and hits the `assetIssueStore.get(name) != null` check, throwing `"Token exists"` ( [1](#0-0) ), permanently denying the victim that asset name — this DoS behavior is exactly what `IssueSameTokenNameAssert` in `AssetIssueActuatorTest` exercises for the "Token exists" failure path: [4](#0-3) .

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

**File:** framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java (L1700-1730)
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
