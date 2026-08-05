## Finding

### Title
Single-step, unconfirmed permission/ownership change in `AccountPermissionUpdateActuator` allows irrecoverable loss of account control - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateContract`, processed by `AccountPermissionUpdateActuator`, lets any account holder replace its `owner`, `witness`, and `active` permissions in a single atomic transaction. The new permission keys are only checked for address *format* validity, not for actual control (e.g., a real private key, or acknowledgment/co-signature by the new key holder). This mirrors the reported bug class exactly: a critical, non-recoverable operation (transferring control of an account/contract) executed in one step with no two-step confirmation, so a mistaken or malicious value permanently locks the caller out with no recovery path.

### Finding Description
`AccountPermissionUpdateActuator.execute` directly overwrites the account's owner/witness/active permissions in one call: [1](#0-0) 

The only checks performed in `validate()`/`checkPermission()` are structural: address format (`DecodeUtil.addressValid`), non-zero weight, threshold ≥ weight sum, key count limits, etc. There is no check that the newly designated owner key is reachable/controlled by anyone, and no acknowledgment step from the new key holder: [2](#0-1) 

Critically, `DecodeUtil.addressValid` only validates address **length** and **prefix byte**, not that the address corresponds to any real, controllable key: [3](#0-2) 

So any 21-byte value starting with the TRON prefix byte (`0x41`) — including a "null-like" address such as `0x41` followed by all zero bytes, or simply a mistyped address whose private key nobody possesses — passes validation. Once `updatePermissions` in `AccountCapsule` commits this new owner permission, the previous owner permission is discarded: [4](#0-3) 

There is no on-chain rollback, no admin override, and no time-locked staging of the new permission set — the change takes effect atomically within the same transaction that authorizes it, exactly analogous to `VotingEscrow.transfer_ownership` setting `self.admin = addr` without verification.

### Impact Explanation
If a user (self-service, unprivileged — no special role required) submits an `AccountPermissionUpdateContract` with an incorrect or unreachable owner-permission address (typo, wrong key, or an address they don't hold the private key for), the account's owner permission is immediately and irreversibly replaced. Because owner permission gates the ability to sign further `AccountPermissionUpdateContract` transactions (and other owner-gated operations), the account becomes permanently locked: no future transaction can be authorized to fix the mistake, and all TRX/TRC10 assets, frozen/staked balances, and voting rights tied to that account become unrecoverable. This is a genuine "invalid/irrecoverable state" impact matching the report's bug class, reachable by any ordinary account holder, not a privileged/trusted role.

### Likelihood Explanation
Likelihood is moderate: this requires user error (mistyping an address, reusing an address without the corresponding key, or malicious social engineering tricking a user into setting an attacker-controlled or bogus key), similar to the original report's exploit scenario where Bob forgets to set `addr` in `transfer_ownership`. Given multisig/permission management is a relatively advanced, error-prone feature and there is no confirmation, staging, or new-key acknowledgment step, the probability of an irreversible mistake is non-trivial, especially through wallets/tools that construct these contracts programmatically.

### Recommendation
- Require a two-step process for owner-permission changes: stage the proposed new permission set, and require a transaction signed under the *new* owner permission (or a time-delayed acceptance) to finalize it, so a malformed/unreachable key set never fully takes effect.
- Optionally, add a mandatory time-lock/grace period during which the previous owner permission can still veto/cancel the pending update.
- At minimum, document loudly (and consider warning in wallets/CLI) that `AccountPermissionUpdateContract` is irrecoverable if the new owner key is not controlled, and consider basic sanity checks (e.g., disallow known "burn"/all-zero-suffix addresses as sole owner key).

### Proof of Concept
1. Alice controls account `A` with default owner permission (key = `A`).
2. Alice (mistakenly, or tricked) builds and signs an `AccountPermissionUpdateContract` for account `A` setting `owner` permission's sole key to address `B` — a value she typed incorrectly or for which she has no private key (any 21-byte value starting with `0x41` passes `DecodeUtil.addressValid`, per `common/src/main/java/org/tron/common/utils/DecodeUtil.java:15-33`).
3. The actuator's `validate()` only checks format-level constraints in `checkPermission()` (`actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java:71-146`) — it passes.
4. `execute()` calls `account.updatePermissions(...)` (`actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java:49-51`, `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java:1301-1320`), overwriting the owner permission in the same atomic transaction — no staging, no acknowledgment from `B`.
5. Because nobody controls `B`'s private key, no subsequent transaction from account `A` can ever be authorized again under owner permission — the account and all its assets are permanently locked, with no on-chain recovery mechanism.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L43-52)
```java
    final AccountPermissionUpdateContract accountPermissionUpdateContract;
    try {
      accountPermissionUpdateContract = any.unpack(AccountPermissionUpdateContract.class);

      byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
      AccountCapsule account = accountStore.get(ownerAddress);
      account.updatePermissions(accountPermissionUpdateContract.getOwner(),
          accountPermissionUpdateContract.getWitness(),
          accountPermissionUpdateContract.getActivesList());
      accountStore.put(ownerAddress, account);
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L105-117)
```java
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
```

**File:** common/src/main/java/org/tron/common/utils/DecodeUtil.java (L15-33)
```java
  public static boolean addressValid(byte[] address) {
    if (ArrayUtils.isEmpty(address)) {
      logger.warn("Warning: Address is empty !!");
      return false;
    }
    if (address.length != ADDRESS_SIZE / 2) {
      logger.warn(
          "Warning: Address length need " + ADDRESS_SIZE + " but " + address.length
              + " !!");
      return false;
    }

    if (address[0] != addressPreFixByte) {
      logger.warn("Warning: Address need prefix with " + addressPreFixByte + " but "
          + address[0] + " !!");
      return false;
    }
    return true;
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
