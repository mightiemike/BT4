### Title
`AccountPermissionUpdateActuator` accepts an unspendable zero-value address as a permission key, permanently bricking account/multisig control - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
The `MainToken.set_mint_multisig()` bug class is "a privileged setter accepts an address value without checking it isn't the zero address, permanently destroying the ability to update a critical permission/role." The closest reachable analog in java-tron is `AccountPermissionUpdateActuator`, which processes the `AccountPermissionUpdateContract` — a transaction any account owner can broadcast to set their `owner`, `witness`, and `active` multisig permissions. Its internal validation, `checkPermission()`, never rejects a key whose address is the all-zero-payload address (`0x41` prefix followed by 20 zero bytes), because the only address check performed is `DecodeUtil.addressValid()`.

### Finding Description
`checkPermission()` validates key count, threshold, weights, and operation bitmap, and calls `DecodeUtil.addressValid()` on each key address: [1](#0-0) 

`DecodeUtil.addressValid()` only checks that the address is 21 bytes long and starts with the correct network prefix byte — it never checks that the remaining 20 bytes are non-zero: [2](#0-1) 

Because of this, a transaction setting the `owner` permission's sole key to the "zero" TRON address (`0x41` + 20 zero bytes) — an address with no known private key, hence unspendable/unsignable — passes both `checkPermission()` and the top-level `validate()`: [3](#0-2) 

`execute()` then commits the new permissions unconditionally via `account.updatePermissions(...)`: [4](#0-3) 

Once the `owner` permission's only signer is the zero address, there is no way to produce a valid signature for that permission again (there is no private key for the zero-payload address), so the account can never submit another `AccountPermissionUpdateContract` to fix its own permission structure — the same "permanently loses admin/multisig control" outcome as `set_mint_multisig(0)` in the original report.

### Impact Explanation
This is directly reachable via a normal, unprivileged broadcast transaction (`AccountPermissionUpdateContract`) — no committee or node-operator privilege is required beyond controlling the account being updated. A user error (or a malicious dApp / wallet crafting a transaction that the user blindly signs) that sets an `owner`/`active` permission key to the zero address permanently locks that account's multisig permission structure, mirroring the "loses `minting_multisig` forever" impact in the report. This is an unauthorized/irrecoverable account-state corruption via the permission-validation/actuator path explicitly in scope.

### Likelihood Explanation
Likelihood is moderate: it requires either user/tooling error when constructing a permission-update transaction, or a maliciously crafted transaction that a signer approves without realizing the key equals the zero address. There's no on-chain protection preventing it — `addressValid()` performs only format/prefix checks, not a zero-value check, and no other validation catches degenerate all-zero keys.

### Recommendation
In `AccountPermissionUpdateActuator.checkPermission()` (and ideally in `DecodeUtil.addressValid()` or a shared helper used across all address-accepting actuators), explicitly reject keys/addresses equal to the zero-value address (`0x41` followed by 20 zero bytes), in addition to the existing prefix/length check, before allowing them to be set as permission keys.

### Proof of Concept
1. Attacker/account owner builds an `AccountPermissionUpdateContract` for `ownerAddress` with:
   - `owner` permission containing a single `Key` whose `address` = `0x41` + 20×`0x00`, `weight` = 1, `threshold` = 1.
   - Valid `active` permission(s) as required by `validate()`.
2. Submit/broadcast the transaction. `AccountPermissionUpdateActuator.validate()` passes: `DecodeUtil.addressValid()` on the zero-payload address returns `true` (prefix and length match), and all other `checkPermission()` checks pass (key count = 1, weight = 1 ≥ threshold = 1).
3. `execute()` calls `account.updatePermissions(...)`, replacing the account's `owner` permission with one whose sole valid signer is the zero address.
4. Because no private key exists for the zero-payload address, the account can never again produce a validly-signed `AccountPermissionUpdateContract` (which requires an `owner`-permission signature), permanently bricking that account's ability to update its own multisig/owner permission — analogous to permanently losing `minting_multisig`.

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

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L212-228)
```java
    if (owner.getType() != PermissionType.Owner) {
      throw new ContractValidateException("owner permission type is error");
    }
    checkPermission(owner);
    if (accountCapsule.getIsWitness()) {
      if (witness.getType() != PermissionType.Witness) {
        throw new ContractValidateException("witness permission type is error");
      }
      checkPermission(witness);
    }
    for (Permission permission : actives) {
      if (permission.getType() != PermissionType.Active) {
        throw new ContractValidateException("active permission type is error");
      }
      checkPermission(permission);
    }
    return true;
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
