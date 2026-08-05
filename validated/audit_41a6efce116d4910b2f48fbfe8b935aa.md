## Title
Front-runnable token-name squatting in `AssetIssueActuator` allows hijacking a pending ICO's price/supply parameters - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
The reported Algebra `initialize()` bug is a class of vulnerability where a state-mutating operation that (a) is unprivileged/callable by anyone, (b) is protected only by a "has this been set yet" guard, and (c) permanently fixes an economically important parameter (a price/ratio) that other unprivileged users will later rely on, can be front-run in the public mempool to hijack that parameter before the legitimate caller's transaction lands.

`AssetIssueActuator` exhibits the same shape: any account can issue a token under an arbitrary name, and — when `AllowSameTokenName == 0` — the name acts exactly like Algebra's `globalState.price == 0` guard: the first successful `AssetIssueContract` for a given name wins and permanently fixes that name's total supply and TRX exchange ratio (`num`/`trxNum`) for the whole ICO period.

### Finding Description
`AssetIssueActuator.doValidate()` only allows a token name to be issued once while same-token-name mode is disabled: [1](#0-0) 

The caller fully controls the economically critical parameters of this one-time action, including the fixed exchange rate between the new asset and TRX: [2](#0-1) 

Once issued, `num`/`trxNum` become immutable ICO economics that other unprivileged users (participants) rely on when buying the asset via `ParticipateAssetIssueActuator`: [3](#0-2) 

Because `ContractType.AssetIssueContract` transactions are broadcast to the public mempool before block inclusion (like any TVM/blockchain transaction), an attacker observing a pending, well-publicized token-issuance transaction (e.g., referenced in a project's announcement or dApp UI) can extract the `name` field and submit a competing `AssetIssueContract` with the identical name, a manipulated `num`/`trxNum` ratio, and a higher fee/priority to get it mined first. Because the name-existence check in [1](#0-0)  is the only uniqueness guard, the legitimate issuer's original transaction then fails with `"Token exists"`, while unsuspecting participants who send TRX expecting to buy the "real" project's token under that name instead purchase the attacker's asset at the attacker-chosen price.

This is confirmed as a permanent, one-time-only assignment: the code additionally guards issuance per-account (`"An account can only issue one asset"`), reinforcing that this is architecturally a create-once/"initialize" pattern rather than a repeatable multi-instance action like `ExchangeCreateActuator` (which allows unlimited independent trading pairs for the same token pair and is therefore not vulnerable to this front-running class, since a racing exchange creation does not block or redirect the legitimate creator's own transaction): [4](#0-3) 

### Impact Explanation
This is a concrete public-facing state/economics-hijack issue: an attacker can preempt a specific token name, permanently claim its supply/price parameters, and cause the legitimate issuer's transaction to revert (denial-of-service on the ICO), while diverting participant funds sent via `ParticipateAssetIssueActuator` to the attacker's token at a price the attacker controls. This matches the "invalid-state/divergence" and "underpriced-public-work" impact classes: the mint of `TotalSupply` units at a fixed `num`/`trxNum` ratio is permanently fixed by whoever wins the name-registration race, not by the intended project. Funds transferred by naive participants who trust the name are effectively captured by the attacker's contract state instead of the legitimate one.

### Likelihood Explanation
Likelihood is moderate: it requires (1) a token-issuance transaction to be publicly announced or otherwise identifiable/predictable by name before confirmation, and (2) `AllowSameTokenName == 0` to be the active mode (this flag is a dynamic/maintenance-controlled chain parameter, and historically was the default before same-token-name support was introduced). The `assetIssueNameTest`/`IssueSameTokenNameAssert` tests confirm the exact preconditions and error paths exist and are exercised in the current codebase: [5](#0-4) 

### Recommendation
- Do not rely solely on a first-come-first-served name check for economically consequential, non-reversible parameter assignment. Consider a commit-reveal scheme for asset-name registration (commit a hash of name+salt, then reveal after a delay) so the name/price cannot be extracted and front-run from the mempool.
- Alternatively, bind name reservation to a prior on-chain registration/deposit by the intended owner address before allowing the full `AssetIssueContract` with supply/price parameters to be finalized.
- At minimum, warn integrators/dApps that token names are not race-safe under `AllowSameTokenName == 0` and that participants must verify the resolved `AssetIssueID`/owner address (not just the display name) before sending TRX via `ParticipateAssetIssueActuator`.

### Proof of Concept
1. Node/dApp observes the public mempool for pending `AssetIssueContract` transactions (`ContractType.AssetIssueContract`) and decodes the `name`, `num`, and `trxNum` fields.
2. Attacker crafts a new `AssetIssueContract` with the same `name`, but with an unfavorable `num`/`trxNum` ratio (e.g., very few tokens per TRX) and submits it with a higher-priority fee so it is included in an earlier block than the victim's transaction.
3. Attacker's transaction succeeds because `assetIssueStore.get(name) == null` at validation time — see the guard at [1](#0-0) .
4. Victim's original transaction, executed afterward, fails validation with `"Token exists"` (same code path), and is dropped/reverted.
5. Users who already saw the announced token name and send TRX via `ParticipateAssetIssueContract` targeting that name now interact with the attacker's asset and receive tokens computed against the attacker's `num`/`trxNum`, per [3](#0-2) , rather than the legitimate project's intended economics.

Note: I was unable to fully verify the current default/mainnet value of `AllowSameTokenName` or confirm whether it has since been permanently forced to `1` on live networks (which would eliminate this attack surface); this would require inspecting `DynamicPropertiesStore` defaults/maintenance history, which was not covered in the code I could retrieve. If `AllowSameTokenName` is now permanently `1` in production, this specific analog would no longer be exploitable, though the underlying pattern (create-once with attacker-controlled economics) would still be worth noting for any future single-instance resources.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L210-214)
```java
    if (dynamicStore.getAllowSameTokenName() == 0
        && assetIssueStore.get(assetIssueContract.getName().toByteArray())
        != null) {
      throw new ContractValidateException("Token exists");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L216-226)
```java
    if (assetIssueContract.getTotalSupply() <= 0) {
      throw new ContractValidateException("TotalSupply must greater than 0!");
    }

    if (assetIssueContract.getTrxNum() <= 0) {
      throw new ContractValidateException("TrxNum must greater than 0!");
    }

    if (assetIssueContract.getNum() <= 0) {
      throw new ContractValidateException("Num must greater than 0!");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L182-188)
```java
      int trxNum = assetIssueCapsule.getTrxNum();
      int num = assetIssueCapsule.getNum();
      long exchangeAmount = multiplyExact(amount, num);
      exchangeAmount = floorDiv(exchangeAmount, trxNum);
      if (exchangeAmount <= 0) {
        throw new ContractValidateException("Can not process the exchange!");
      }
```

**File:** framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java (L1474-1524)
```java
  /**
   * an account should issue asset only once
   */
  @Test
  public void assetIssueNameTest() {
    Any contract = Any.pack(
        AssetIssueContract.newBuilder()
            .setOwnerAddress(ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS)))
            .setName(ByteString.copyFromUtf8(NAME)).setTotalSupply(TOTAL_SUPPLY).setTrxNum(TRX_NUM)
            .setNum(NUM)
            .setStartTime(startTime).setEndTime(endTime)
            .setDescription(ByteString.copyFromUtf8("description"))
            .setUrl(ByteString.copyFromUtf8(URL)).build());
    AssetIssueActuator actuator = new AssetIssueActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(contract);

    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      actuator.validate();
      actuator.execute(ret);
    } catch (ContractValidateException e) {
      Assert.assertFalse(e instanceof ContractValidateException);
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }

    contract = Any.pack(
        AssetIssueContract.newBuilder()
            .setOwnerAddress(ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS)))
            .setName(ByteString.copyFromUtf8(ASSET_NAME_SECOND)).setTotalSupply(TOTAL_SUPPLY)
            .setTrxNum(TRX_NUM)
            .setNum(NUM).setStartTime(startTime).setEndTime(endTime)
            .setDescription(ByteString.copyFromUtf8("description"))
            .setUrl(ByteString.copyFromUtf8(URL)).build());
    actuator = new AssetIssueActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(contract);

    ret = new TransactionResultCapsule();
    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.assertTrue(false);
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("An account can only issue one asset", e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    } finally {
      dbManager.getAssetIssueStore().delete(ByteArray.fromString(NAME));
      dbManager.getAssetIssueStore().delete(ByteArray.fromString(ASSET_NAME_SECOND));
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java (L1697-1730)
```java
  /**
   * repeat issue assert name,
   */
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
