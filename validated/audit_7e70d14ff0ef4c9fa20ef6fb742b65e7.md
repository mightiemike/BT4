Confirmed: `AccountIdIndexStore` has no delete/remove method — once an accountId key is written via `put`, it is permanent for the life of the chain (aside from chain revoking/rollback mechanics), and lookups are case-insensitive (`getLowerCaseAccountId`). Combined with `SetAccountIdActuator.calcFee()` returning `0`, this makes the griefing attack essentially free and irreversible.

### Title
Front-runnable, permanent, fee-free squatting of user-chosen `accountId` values blocks legitimate users - (File: `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java`)

### Summary
`SetAccountIdActuator` lets any account attach an arbitrary, user-chosen `accountId` to itself exactly once, provided the ID string is not already claimed. Because there is no binding between `msg.sender`/`ownerAddress` and the requested `accountId` value at submission time, and because claims are permanent and free, any observer of the transaction pool can front-run a victim's `SetAccountIdContract` transaction, claim the same `accountId` for their own account, and permanently deny the victim that identifier — mirroring the `createOffer` ID-squatting bug class described in the report (checking-then-reverting on a caller-chosen unique ID with no sender/ID binding).

### Finding Description
`SetAccountIdActuator.validate()` performs these checks, in order: the account exists, has no `accountId` set yet, and the requested `accountId` is not already present in `AccountIdIndexStore`: [1](#0-0) 

`execute()` then unconditionally sets the `accountId` on the caller's account and indexes it: [2](#0-1) 

The only validation applied to the `accountId` value itself is length/character-set (`TransactionUtil.validAccountId`, 8–32 printable-ASCII bytes): [3](#0-2) 

There is no mechanism binding a specific `accountId` to a specific `ownerAddress` before submission — anyone can submit `SetAccountIdContract{ownerAddress: attacker, accountId: "BOB_ID"}`. `AccountIdIndexStore.put`/`has` normalize the ID to lowercase and store it permanently with no delete/removal API in the store: [4](#0-3) 

Because `calcFee()` for this actuator is `0`, the attack has no monetary cost beyond ordinary bandwidth/energy for a trivial transaction: [5](#0-4) 

Attack flow (directly analogous to the report's `createOffer` ID-squatting):
1. Bob broadcasts `SetAccountIdContract{ownerAddress: Bob, accountId: "BOB_ID"}`.
2. Attacker Alice observes this in the mempool, and broadcasts her own `SetAccountIdContract{ownerAddress: Alice, accountId: "BOB_ID"}` with a higher fee/priority so it lands first.
3. Alice's transaction succeeds; `AccountIdIndexStore.has("BOB_ID")` now returns true.
4. Bob's transaction reverts with `"This id has existed"`. Since an account can only ever set its `accountId` once (`"This account id already set"` guard) and the index entry is never deleted, `"BOB_ID"` is permanently unusable by Bob or anyone else, at zero cost to Alice.

### Impact Explanation
This is a state/accounting-integrity griefing vector: it lets any unprivileged actor permanently deny another user a specific human-readable account identifier, with no way to reclaim or free it afterward (no revocation path exists in `AccountIdIndexStore`). Unlike the original report's offer-ID case (which at least required allowance funds and only blocked one offer/instance), this java-tron analog costs the attacker nothing (fee = 0) and the damage is irreversible for the lifetime of the chain, so it can be used at scale to squat/deny large numbers of desired IDs cheaply.

### Likelihood Explanation
Any account holder can call `SetAccountIdContract` — no privileged role required. TRON transactions are publicly visible pre-confirmation, and front-running via priority fee/latency is a well-known, low-effort technique on TRON just as on other public chains, making this readily executable by any motivated actor.

### Recommendation
Bind the `accountId` claim to the intended `ownerAddress` before broadcast (e.g., require a signature/commitment from the requesting account matching the chosen ID, or use a commit-reveal scheme), or otherwise remove the purely first-come-first-served, fee-free, permanent global uniqueness constraint on `accountId` in `SetAccountIdActuator`/`AccountIdIndexStore`.

### Proof of Concept
1. Deploy two accounts, `alice` and `bob`, both funded enough to broadcast a `SetAccountIdContract`.
2. `bob` prepares (but does not yet broadcast) `SetAccountIdContract{ownerAddress: bob, accountId: "BOB_ID_1"}`.
3. Simulate front-running: broadcast/execute `alice`'s `SetAccountIdActuator` first with `SetAccountIdContract{ownerAddress: alice, accountId: "BOB_ID_1"}` — succeeds (as shown by `SetAccountIdActuatorTest.nameAlreadyUsed` demonstrating the "This id has existed" validation path): [6](#0-5) 
4. Now execute `bob`'s identical actuator call — it throws `ContractValidateException("This id has existed")`, permanently blocking `bob` from ever using `"BOB_ID_1"` since `AccountIdIndexStore` has no delete API and `bob`'s account can only set an `accountId` once.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L45-51)
```java
    byte[] ownerAddress = setAccountIdContract.getOwnerAddress().toByteArray();
    AccountCapsule account = accountStore.get(ownerAddress);

    account.setAccountId(setAccountIdContract.getAccountId().toByteArray());
    accountStore.put(ownerAddress, account);
    accountIdIndexStore.put(account);
    ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L87-96)
```java
    AccountCapsule account = accountStore.get(ownerAddress);
    if (account == null) {
      throw new ContractValidateException("Account has not existed");
    }
    if (account.getAccountId() != null && !account.getAccountId().isEmpty()) {
      throw new ContractValidateException("This account id already set");
    }
    if (accountIdIndexStore.has(accountId)) {
      throw new ContractValidateException("This id has existed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L106-109)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L63-86)
```java
  private static final int MAX_ACCOUNT_ID_LEN = 32;
  private static final int MIN_ACCOUNT_ID_LEN = 8;
  private static final int MAX_ASSET_NAME_LEN = 32;
  private static final int MAX_TOKEN_ABBR_NAME_LEN = 5;
  private static final int MAX_ASSET_DESCRIPTION_LEN = 200;
  private static final int MAX_URL_LEN = 256;

  @Autowired
  private ChainBaseManager chainBaseManager;

  public static boolean validAccountName(byte[] accountName) {
    return validBytes(accountName, MAX_ACCOUNT_NAME_LEN, true);
  }

  public static boolean validAssetDescription(byte[] description) {
    return validBytes(description, MAX_ASSET_DESCRIPTION_LEN, true);
  }

  public static boolean validUrl(byte[] url) {
    return validBytes(url, MAX_URL_LEN, false);
  }

  public static boolean validAccountId(byte[] accountId) {
    return validReadableBytes(accountId, MAX_ACCOUNT_ID_LEN) && accountId.length >= MIN_ACCOUNT_ID_LEN;
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L16-57)
```java
public class AccountIdIndexStore extends TronStoreWithRevoking<BytesCapsule> {

  @Autowired
  public AccountIdIndexStore(@Value("accountid-index") String dbName) {
    super(dbName);
  }

  private static byte[] getLowerCaseAccountId(byte[] bsAccountId) {
    return ByteString
        .copyFromUtf8(ByteString.copyFrom(bsAccountId).toStringUtf8().toLowerCase(Locale.ROOT))
        .toByteArray();
  }

  public void put(AccountCapsule accountCapsule) {
    byte[] lowerCaseAccountId = getLowerCaseAccountId(accountCapsule.getAccountId().toByteArray());
    super.put(lowerCaseAccountId, new BytesCapsule(accountCapsule.getAddress().toByteArray()));
  }

  public byte[] get(ByteString name) {
    BytesCapsule bytesCapsule = get(name.toByteArray());
    if (Objects.nonNull(bytesCapsule)) {
      return bytesCapsule.getData();
    }
    return null;
  }

  @Override
  public BytesCapsule get(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    if (ArrayUtils.isEmpty(value)) {
      return null;
    }
    return new BytesCapsule(value);
  }

  @Override
  public boolean has(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    return !ArrayUtils.isEmpty(value);
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/SetAccountIdActuatorTest.java (L173-217)
```java
  @Test
  public void nameAlreadyUsed() {
    TransactionResultCapsule ret = new TransactionResultCapsule();
    SetAccountIdActuator actuator = new SetAccountIdActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS));
    SetAccountIdActuator actuator1 = new SetAccountIdActuator();
    actuator1.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS_1));
    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.assertEquals(ret.getInstance().getRet(), code.SUCESS);
      AccountCapsule accountCapsule = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(ACCOUNT_NAME, accountCapsule.getAccountId().toStringUtf8());
      Assert.assertTrue(true);
    } catch (ContractValidateException e) {
      logger.info(e.getMessage());
      Assert.assertFalse(e instanceof ContractValidateException);
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }

    AccountCapsule ownerCapsule =
        new AccountCapsule(
            ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS_1)),
            ByteString.EMPTY,
            AccountType.Normal);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    try {
      actuator1.validate();
      actuator1.execute(ret);
      Assert.assertFalse(true);
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("This id has existed", e.getMessage());
      AccountCapsule accountCapsule = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(ACCOUNT_NAME, accountCapsule.getAccountId().toStringUtf8());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
