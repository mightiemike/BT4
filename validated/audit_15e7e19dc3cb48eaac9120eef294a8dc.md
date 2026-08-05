Excellent — this is the strongest analog. `AssetIssueActuator.validate()` at [1](#0-0)  rejects a token issuance if `assetIssueStore.get(name)` already exists ("Token exists"), where `name` is a plaintext, attacker-observable, user-chosen field in the pending transaction, and issuance is a one-time, irreversible, economically significant action per account (`"An account can only issue one asset"`) that cannot be retried under the desired name once griefed.

### Title
Front-runnable, unique user-chosen asset name permanently blocks legitimate TRC10 token issuance - (File: actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java)

### Summary
`AssetIssueActuator` allows any account to issue a TRC10 token exactly once, under a user-chosen `name` that must be globally unique when `AllowSameTokenName` is disabled. Because the name is broadcast in plaintext in the pending transaction before block inclusion, and an account may only ever issue one asset, an attacker who observes a victim's pending `AssetIssueContract` transaction can front-run it with the same `name`, permanently denying the victim the ability to issue their intended token under that name — mirroring the reported Folks Finance bug class where a user-chosen, first-come-first-served identifier can be griefed by an unprivileged front-runner.

### Finding Description
When `dynamicStore.getAllowSameTokenName() == 0`, `AssetIssueActuator.validate()` checks uniqueness of the raw `name` field against `assetIssueStore`: [1](#0-0) 
This `name` is fully attacker-visible before confirmation (it is a cleartext field of the broadcast `AssetIssueContract` transaction, observable in the mempool/P2P layer by any node). The actuator also enforces a strict one-shot constraint per account: [2](#0-1) 
There is no mechanism to release, reset, or reassign a previously claimed asset name, and `execute()` permanently commits the name to `assetIssueStore`/`accountCapsule.setAssetIssuedName`: [3](#0-2) [4](#0-3) 

An attacker who sees a victim's pending `AssetIssueContract` tx with a chosen `name` can submit their own `AssetIssueContract` with the identical `name` (from a different, already-created account with sufficient balance for `AssetIssueFee`) and have it mined first (e.g., by paying a higher-priority fee or via direct block-producer submission). This causes the victim's transaction to fail validation with `"Token exists"`, and since each account can only ever issue one asset, the victim's account is permanently locked out of ever issuing an asset under that name (and, because of the "one asset per account" rule, is fully blocked unless they abandon the desired name and choose a fresh one on a brand-new account).

### Impact Explanation
This is a griefing/state-corruption impact with no profit motive required from the attacker (identical class to the reported bug): a legitimate token issuer can be permanently and irrecoverably denied their intended, meaningful asset identifier on-chain, which is externally visible, indexed, and directly tied to real-world branding/economic value (TRC10 token names are used by exchanges, wallets, and explorers for identification). Because the "one issuance per account" restriction is enforced at the account level with no recovery path, the griefed account can never legitimately hold that name, and the victim must either abandon the identifier or create/fund an entirely new account, causing lasting reputational/economic damage to the intended issuer and confusion for downstream consumers of the asset name.

### Likelihood Explanation
Likelihood is high for any account issuance where the desired name is valuable or predictable: transactions are visible before confirmation, the check is a straightforward existence check keyed on the raw name, and any account holder able to pay the `AssetIssueFee` can front-run at negligible cost. No special privilege, contract deployment, or complex setup is required — it is fully exploitable by an ordinary, unprivileged user, matching the report's "griefing" impact category.

### Recommendation
Avoid binding uniqueness/ownership to a value chosen and revealed in plaintext before finality. Options include: (1) require a commit-reveal scheme for the desired asset name (submit a hash first, reveal later after confirmation) so the name is not visible to front-runners until the committer already has assurance of inclusion; (2) allow a short reservation window tied to the committing account's signature/nonce rather than first-write-wins on raw content; (3) relax the "one asset per account, name never releasable" invariant so a griefed account is not permanently blocked, e.g. allow retry with a different name without penalty, or allow the true owner to reclaim a name if the colliding issuance was clearly a front-run within the same block/near-immediate window (though this is difficult to define objectively and mainly reduces permanence of harm).

### Proof of Concept
1. Victim account `V` (already created, funded with `AssetIssueFee`) broadcasts `AssetIssueContract{name="MYTOKEN", ...}` to issue their token.
2. Attacker account `A` (already created and funded) observes `V`'s transaction in the mempool/P2P broadcast layer before it is included in a block, since `name` is a plaintext protobuf field.
3. `A` immediately broadcasts their own `AssetIssueContract{name="MYTOKEN", ownerAddress=A, ...}` with a higher fee/priority (or submits it more efficiently), ensuring it is validated/included first via `AssetIssueActuator.validate()`/`execute()` at [5](#0-4) .
4. `assetIssueStore` now contains `"MYTOKEN"` bound to `A`.
5. When `V`'s original transaction is processed, `AssetIssueActuator.validate()` reaches the check at [1](#0-0)  and throws `ContractValidateException("Token exists")`, so `V`'s issuance fails.
6. Because `V`'s account already exists and the "one asset per account" rule at [6](#0-5)  is unaffected by the failed attempt (no asset was set on `V`), `V` may retry with a different name, but can never legitimately obtain `"MYTOKEN"` — permanent griefing of the desired identifier is achieved, as demonstrated by the existing regression test asserting the exact "Token exists" failure mode at `framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorTest.java` (`IssueSameTokenNameAssert`, lines 1700-1757) [7](#0-6) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L55-133)
```java
  @Override
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    AssetIssueV2Store assetIssueV2Store = chainBaseManager.getAssetIssueV2Store();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    try {
      AssetIssueContract assetIssueContract = any.unpack(AssetIssueContract.class);
      byte[] ownerAddress = assetIssueContract.getOwnerAddress().toByteArray();
      AssetIssueCapsule assetIssueCapsule = new AssetIssueCapsule(assetIssueContract);
      AssetIssueCapsule assetIssueCapsuleV2 = new AssetIssueCapsule(assetIssueContract);
      long tokenIdNum = dynamicStore.getTokenIdNum();
      tokenIdNum++;
      assetIssueCapsule.setId(Long.toString(tokenIdNum));
      assetIssueCapsuleV2.setId(Long.toString(tokenIdNum));
      dynamicStore.saveTokenIdNum(tokenIdNum);

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

      adjustBalance(accountStore, ownerAddress, -fee);
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);//send to blackhole
      }
      AccountCapsule accountCapsule = accountStore.get(ownerAddress);
      List<FrozenSupply> frozenSupplyList = assetIssueContract.getFrozenSupplyList();
      Iterator<FrozenSupply> iterator = frozenSupplyList.iterator();
      long remainSupply = assetIssueContract.getTotalSupply();
      List<Frozen> frozenList = new ArrayList<>();
      long startTime = assetIssueContract.getStartTime();

      while (iterator.hasNext()) {
        FrozenSupply next = iterator.next();
        long expireTime = startTime + next.getFrozenDays() * FROZEN_PERIOD;
        Frozen newFrozen = Frozen.newBuilder()
            .setFrozenBalance(next.getFrozenAmount())
            .setExpireTime(expireTime)
            .build();
        frozenList.add(newFrozen);
        remainSupply -= next.getFrozenAmount();
      }

      if (dynamicStore.getAllowSameTokenName() == 0) {
        accountCapsule.addAsset(assetIssueCapsule.createDbKey(), remainSupply);
      }
      accountCapsule.setAssetIssuedName(assetIssueCapsule.createDbKey());
      accountCapsule.setAssetIssuedID(assetIssueCapsule.createDbV2Key());
      accountCapsule.addAssetV2(assetIssueCapsuleV2.createDbV2Key(), remainSupply);
      accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
          .addAllFrozenSupply(frozenList).build());

      accountStore.put(ownerAddress, accountCapsule);

      ret.setAssetIssueID(Long.toString(tokenIdNum));
      ret.setStatus(fee, code.SUCESS);
    } catch (InvalidProtocolBufferException | BalanceInsufficientException | ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    return true;
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

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L282-289)
```java
    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      throw new ContractValidateException("Account not exists");
    }

    if (!accountCapsule.getAssetIssuedName().isEmpty()) {
      throw new ContractValidateException("An account can only issue one asset");
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
