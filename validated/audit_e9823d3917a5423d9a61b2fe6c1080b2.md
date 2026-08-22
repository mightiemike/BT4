### Title
`AccountPermissionUpdateContract` allows irreversible, single-step overwrite of an account's owner/active/witness permissions - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateActuator` implements TRON's multi-sig permission system by letting an account's current owner permission holder replace the account's `Owner`, `Active`, and (if a witness) `Witness` permissions in a single transaction, with no staged/"pending" confirmation step and no on-chain check that the new keys are actually controlled by anyone reachable.

### Finding Description
`execute()` directly overwrites the account's permission set from the submitted contract in one atomic step: [1](#0-0) . The `validate()` path only checks structural well-formedness of the new permissions via `checkPermission` — valid addresses, distinct keys, weight sums meeting the threshold, key/permission count bounds, and (for `Active`) a valid operations bitmap — but never verifies that the new owner key(s) are controllable, reachable, or different from an unintended/mistyped address: [2](#0-1) . The only precondition checks are on the current owner address/account existence and permission shape, not on the sanity of the destination: [3](#0-2) . Because this update is applied immediately and unconditionally via `account.updatePermissions(...)` in `AccountCapsule`, there is no "pending owner" state and no claim/accept step analogous to a two-step ownership transfer — mirroring exactly the single-step transfer risk described in the external report, but here it is a self-service, unprivileged operation reachable by any TRON account owner via a broadcast transaction.

### Impact Explanation
If the owner permission is updated with a mistyped address, a key nobody holds the private key for, or a threshold/weight configuration that can never be satisfied (e.g., total weight of reachable keys below threshold once one key is unrecoverable), the account's owner and active permissions are permanently and irreversibly bricked: no future `AccountPermissionUpdateContract`, transfer, or contract call requiring owner/active signatures can ever be authorized again, since the actuator that would fix the permissions itself requires a valid owner signature to execute (self-referential lock-out). For a witness account, the witness permission can be similarly bricked, which can affect block production/signing capability for that witness. This is a permanent, non-recoverable loss of control over the account's funds and TRC-10/smart-contract interactions gated by owner/active permissions.

### Likelihood Explanation
Low, since it requires the account's legitimate owner to submit a malformed/incorrect `AccountPermissionUpdateContract` (e.g., wrong address, unreachable weight/threshold combination, or losing the corresponding private key), similar to the "requires an error on the admin side" likelihood noted in the original report. However, unlike the original report's admin-only scope, this is not limited to a privileged role — it is a standard user-facing multi-sig feature exposed to any account, meaning the exposure surface (every TRON account with `AllowMultiSign` enabled) is far larger.

### Recommendation
Introduce a two-step permission update flow for `AccountPermissionUpdateContract`: stage the new `Owner`/`Active`/`Witness` permissions as "pending" and require a subsequent transaction signed under the new permission set (i.e., proof that the new keys can actually produce valid signatures satisfying the new threshold) before the pending permissions replace the active ones. Alternatively, at minimum, require the update transaction itself to be co-signed/validated against the *new* permission set's threshold (a "self-signing" proof) in addition to the current owner signature, so that permissions provably reachable by their claimed keys are the only ones ever committed.

### Proof of Concept
1. Account `A` has `AllowMultiSign` enabled and calls `AccountPermissionUpdateContract` with `ownerAddress = A`.
2. The submitted `Owner` permission sets keys to an address `X` that `A` mistyped or does not control (address passes `DecodeUtil.addressValid` since it's just a well-formed 21-byte address), with threshold satisfied purely by that key's weight.
3. `validate()` passes because `checkPermission` only checks structural validity (distinct, valid addresses, weight ≥ threshold), not key custody: [4](#0-3) .
4. `execute()` commits the new permissions immediately: [5](#0-4) .
5. Account `A` can no longer produce a valid owner-permission signature for any future transaction (including a corrective `AccountPermissionUpdateContract`), permanently bricking owner/active-gated operations on the account.

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

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L178-220)
```java
    byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("invalidate ownerAddress");
    }
    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      throw new ContractValidateException("ownerAddress account does not exist");
    }

    if (!accountPermissionUpdateContract.hasOwner()) {
      throw new ContractValidateException("owner permission is missed");
    }

    if (accountCapsule.getIsWitness()) {
      if (!accountPermissionUpdateContract.hasWitness()) {
        throw new ContractValidateException("witness permission is missed");
      }
    } else {
      if (accountPermissionUpdateContract.hasWitness()) {
        throw new ContractValidateException("account isn't witness can't set witness permission");
      }
    }

    if (accountPermissionUpdateContract.getActivesCount() == 0) {
      throw new ContractValidateException("active permission is missed");
    }
    if (accountPermissionUpdateContract.getActivesCount() > 8) {
      throw new ContractValidateException("active permission is too many");
    }

    Permission owner = accountPermissionUpdateContract.getOwner();
    Permission witness = accountPermissionUpdateContract.getWitness();
    List<Permission> actives = accountPermissionUpdateContract.getActivesList();

    if (owner.getType() != PermissionType.Owner) {
      throw new ContractValidateException("owner permission type is error");
    }
    checkPermission(owner);
    if (accountCapsule.getIsWitness()) {
      if (witness.getType() != PermissionType.Witness) {
        throw new ContractValidateException("witness permission type is error");
      }
      checkPermission(witness);
```
