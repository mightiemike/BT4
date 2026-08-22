### Title
Front-Running DoS on `SetAccountIdContract` Permanently Blocks Legitimate Account ID Registration - (File: `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java`)

### Summary
`SetAccountIdActuator` lets any account holder claim a globally unique, case-insensitive `accountId` via a broadcast transaction, with no fee (`calcFee()` returns `0`) and no relationship enforced between the caller and the requested `accountId` string. Because the `accountId`-to-address mapping is a first-come, first-served global index and, once set, can *never* be changed by the legitimate owner, an attacker can observe a pending `SetAccountIdContract` transaction in the mempool and front-run it with the identical `accountId`, permanently denying the original submitter that identifier — the same "front-run a to-be-claimed unique identifier with no cost or authentication" bug class described in the report for `createNewTask`.

### Finding Description
`SetAccountIdActuator.validate()` only checks that the calling account exists, hasn't already set an id, and that the requested `accountId` is not already present in `AccountIdIndexStore`: [1](#0-0) 

There is no binding between the requester and the specific `accountId` value prior to submission (e.g. no commit-reveal, no reservation, no fee scaling with contention), and `calcFee()` is hard-coded to `0`: [2](#0-1) 

The uniqueness constraint is enforced globally and case-insensitively by `AccountIdIndexStore`: [3](#0-2) 

Once an account's id is set, it can never be reset by that same actuator (`account.getAccountId() != null && !isEmpty()` → `"This account id already set"`), making the loss of a chosen `accountId` to a front-runner effectively permanent for that owner: [4](#0-3) 

An attacker monitoring the P2P mempool for a pending `SetAccountIdContract` transaction can extract the `accountId` field (transaction contents are public before confirmation) and submit their own `SetAccountIdContract` with the same `accountId` and a higher `energy`/priority so it is included first, at essentially zero cost (bandwidth-only, since `calcFee()==0`). This mirrors the reported analog: an unauthenticated, publicly-broadcastable transaction that lets anyone claim a scarce, user-facing identifier ahead of the legitimate submitter, causing that submitter's later `"This id has existed"` rejection — a permanent DoS on that identifier for the intended owner.

### Impact Explanation
`accountId` is used as a public lookup key (e.g. `Wallet.getAccountById`) that external/off-chain systems and exchanges rely on to resolve an account by a human-chosen identifier: [5](#0-4) 

A griefer can permanently deny any account the ability to register a meaningful/branded `accountId` for negligible cost, and — since the id cannot later be reassigned by the rightful owner via this actuator — the denial is irreversible for that specific string, enabling extortion or targeted disruption of identity-dependent integrations (similar in kind, though smaller in blast radius, to the reported `batchMerkleRoot` task hijack).

### Likelihood Explanation
The attack requires only standard TRON account access (no special privilege), mempool visibility of pending transactions (routine for any full node or public API user), and negligible transaction cost since `calcFee()` returns `0`. This makes the attack cheap and repeatable against any targeted `accountId`.

### Recommendation
- Key the uniqueness reservation by `(ownerAddress, accountId)` intent established beforehand (e.g., commit-reveal scheme), or bind the fee/cost to discourage speculative squatting.
- Consider allowing the legitimate owner to reclaim/override an id if it was set to an address that never intended to compete for it, or add an anti-frontrunning delay/priority scheme.
- At minimum, document and rate-limit `SetAccountIdContract` submissions and consider requiring a non-trivial fee proportional to potential griefing value, rather than `0`.

### Proof of Concept
1. Alice broadcasts `SetAccountIdContract{ownerAddress: Alice, accountId: "AliceBrand"}`.
2. Attacker observes this transaction in the mempool before it is packed into a block, extracts `accountId = "AliceBrand"`.
3. Attacker broadcasts `SetAccountIdContract{ownerAddress: Attacker, accountId: "AliceBrand"}` with sufficient bandwidth/priority so it is confirmed first.
4. `AccountIdIndexStore.has("aliceb rand")` (lower-cased) now returns true for the attacker's mapping.
5. Alice's original transaction executes afterward and fails validation with `"This id has existed"` per `SetAccountIdActuator.validate()` line 94-96; since Alice's own `getAccountId()` remains unset, she can retry with a different string, but `"AliceBrand"` is permanently unavailable to her going forward.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L23-57)
```java
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L381-392)
```java
  public Account getAccountById(Account account) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    AccountIdIndexStore accountIdIndexStore = chainBaseManager.getAccountIdIndexStore();
    byte[] address = accountIdIndexStore.get(account.getAccountId());
    if (address == null) {
      return null;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    if (accountCapsule == null) {
      return null;
    }
    accountCapsule.importAllAsset();
```
