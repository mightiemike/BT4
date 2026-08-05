### Title
Single-step `AccountPermissionUpdate` allows irrevocable loss/transfer of account authority with no two-step confirmation - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateActuator` lets any account holder replace their account's `Owner`, `Witness`, and `Active` permissions (i.e., which keys/addresses control the account and with what threshold) in a single atomic transaction. There is no two-step "propose then accept" mechanism analogous to `approve`/`transferFrom`: the new permission set takes effect immediately upon execution, with no confirmation step from the new key holder(s). This mirrors the reported bug class in the external report — critical, irrevocable role/address changes performed in one function call instead of a safer two-step procedure.

### Finding Description
`AccountPermissionUpdateActuator.execute()` unpacks the `AccountPermissionUpdateContract` and immediately calls `AccountCapsule.updatePermissions()`, overwriting the account's owner permission (and witness/active permissions) in-place: [1](#0-0) 

The underlying capsule method directly replaces the stored `Permission` objects with the caller-supplied ones with no verification that the new key(s) can act on behalf of the account, nor any staged/pending state requiring a second confirming transaction from the new keys: [2](#0-1) 

`validate()` only checks structural correctness of the new permissions (key count, threshold bounds, weight sums, address format, operation bitmap) — it never checks that the new owner/active/witness keys are reachable, controlled by a party expecting the change, or requires any acknowledgment step: [3](#0-2) 

This is the same root cause described in the report: a single function call performs a critical, security-relevant replacement of a controlling address/role, with no propose-then-accept step to catch mistakes before they become irreversible. Unlike the report's `WithdrawalDelayer`/`HermezAuctionProtocol` examples (privileged governance setters), this is exposed to every unprivileged TRON account holder as a routine, permissionless operation (any account can submit an `AccountPermissionUpdateContract` for itself), so it is a valid unprivileged-user analog rather than a trusted-role-only issue.

### Impact Explanation
If a user (or a wallet/dApp constructing the transaction on the user's behalf) submits an `AccountPermissionUpdateContract` with an incorrect address in the new `Owner` permission's key list (e.g., a typo, wrong-network address, or an address whose private key is unknown to the user), authority over the account transfers immediately and irrevocably to that address once the transaction is included in a block. There is no recovery path — the old owner permission is fully overwritten, and TRON has no rollback/emergency-recovery mechanism for account permissions. This is a direct, concrete impact on **auth**: permanent transfer of authorization to control the account's balance, resources, and signing.

### Likelihood Explanation
This is triggerable by any account under normal, permissionless usage — it does not require any elevated role, and does not require exploiting a bug beyond the missing safety step. Given that permission updates involve manually specifying byte-string addresses (frequently encoded/copy-pasted), and multi-sig configuration (thresholds/weights across owner/active/witness permissions) is inherently complex, user error is a realistic and recurring risk rather than a purely theoretical one. Malicious front-ends/dApps could also exploit this to trick a signer into unknowingly demoting their own key weight or handing over control, since there is no second confirming step from the "new" permission holder to catch mistakes or manipulation before the change is finalized.

### Recommendation
- Short term: Introduce a two-step confirmation process for `AccountPermissionUpdateContract`, similar to an approve/accept pattern — the account proposes the new `Owner`/`Witness`/`Active` permissions, and the transaction only finalizes once a subsequent transaction, signed by (a sufficient threshold of) the *new* keys, confirms acceptance of the role. Alternatively, add a timelock/delay window during which the previous permission set can still countermand the pending change.
- Long term: Document, for every `AccountPermissionUpdateContract`-style operation, the concrete risk of irrevocable authority loss, and consider tooling (e.g., wallet-side dry-run/simulation, address confirmation flows) to reduce user error before submission.

### Proof of Concept
1. Account `A` (owner permission key = `k1`) wants to add a co-signer, so it builds an `AccountPermissionUpdateContract` with a new `Owner` permission listing `k1` and `k2_typo` (an address that was mistyped and is not actually controlled by anyone the user intended).
2. `A` signs and broadcasts the transaction with a valid signature from `k1` (which still satisfies the *old* threshold), so `validate()` passes all structural checks in `AccountPermissionUpdateActuator.validate()`.
3. `execute()` calls `account.updatePermissions(...)`, overwriting the owner permission on-chain with the new (erroneous) key set — see `AccountPermissionUpdateActuator.java` lines 47-52 and `AccountCapsule.java` lines 1301-1320.
4. Depending on the configured threshold, `A` may now be unable to reach the signing threshold required to control its own account (if `k2_typo`'s weight was necessary), and there is no way to submit a corrective `AccountPermissionUpdateContract` because doing so itself requires meeting the now-broken threshold — the account is permanently locked, with no on-chain recovery mechanism.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L47-52)
```java
      byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
      AccountCapsule account = accountStore.get(ownerAddress);
      account.updatePermissions(accountPermissionUpdateContract.getOwner(),
          accountPermissionUpdateContract.getWitness(),
          accountPermissionUpdateContract.getActivesList());
      accountStore.put(ownerAddress, account);
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L71-122)
```java
  private boolean checkPermission(Permission permission) throws ContractValidateException {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (permission.getKeysCount() > dynamicStore.getTotalSignNum()) {
      throw new ContractValidateException("number of keys in permission should not be greater "
          + "than " + dynamicStore.getTotalSignNum());
    }
    if (permission.getKeysCount() == 0) {
      throw new ContractValidateException("key's count should be greater than 0");
    }
    if (permission.getType() == PermissionType.Witness && permission.getKeysCount() != 1) {
      throw new ContractValidateException("Witness permission's key count should be 1");
    }
    if (permission.getThreshold() <= 0) {
      throw new ContractValidateException("permission's threshold should be greater than 0");
    }
    String name = permission.getPermissionName();
    if (!StringUtils.isEmpty(name) && name.length() > 32) {
      throw new ContractValidateException("permission's name is too long");
    }
    //check owner name ?
    if (permission.getParentId() != 0) {
      throw new ContractValidateException("permission's parent should be owner");
    }

    long weightSum = 0;
    List<ByteString> addressList = permission.getKeysList()
        .stream()
        .map(x -> x.getAddress())
        .distinct()
        .collect(toList());
    if (addressList.size() != permission.getKeysList().size()) {
      throw new ContractValidateException(
          "address should be distinct in permission " + permission.getType());
    }
    for (Key key : permission.getKeysList()) {
      if (!DecodeUtil.addressValid(key.getAddress().toByteArray())) {
        throw new ContractValidateException("key is not a validate address");
      }
      if (key.getWeight() <= 0) {
        throw new ContractValidateException("key's weight should be greater than 0");
      }
      try {
        weightSum = addExact(weightSum, key.getWeight());
      } catch (ArithmeticException e) {
        throw new ContractValidateException(e.getMessage());
      }
    }
    if (weightSum < permission.getThreshold()) {
      throw new ContractValidateException(
          "sum of all key's weight should not be less than threshold in permission " + permission
              .getType());
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1301-1320)
```java
  public void updatePermissions(Permission owner, Permission witness, List<Permission> actives) {
    Builder builder = this.account.toBuilder();

    owner = owner.toBuilder().setId(0).build();
    builder.setOwnerPermission(owner);
    if (witness != null && builder.getIsWitness()) {
      witness = witness.toBuilder().setId(1).build();
      builder.setWitnessPermission(witness);
    }

    builder.clearActivePermission();
    if (actives != null) {
      for (int i = 0; i < actives.size(); i++) {
        Permission permission = actives.get(i).toBuilder().setId(i + 2).build();
        builder.addActivePermission(permission);
      }
    }

    this.account = builder.build();
  }
```
