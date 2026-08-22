### Title
Missing zero-address check on multisig permission keys in `AccountPermissionUpdateActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
The Lido report flags `DepositSecurityModule.addGuardian()` for accepting `address(0)` into a privileged address list with no explicit zero-address rejection. The java-tron analog is `AccountPermissionUpdateActuator.checkPermission()`, which validates every key address in an account's `Permission` (owner/witness/active) purely through `DecodeUtil.addressValid()` — a check that only verifies address length and the network prefix byte, never rejecting the "zero" address (prefix byte followed by 20 zero bytes).

### Finding Description
`AccountPermissionUpdateContract` lets any account owner (via a normal broadcast transaction) rewrite its own owner/witness/active permissions, including the list of `Key { address, weight }` entries used later for multisig signature-weight checks. `checkPermission()` enforces distinctness, weight bounds, and address *format* validity, but the only address check is: [1](#0-0) 
and `DecodeUtil.addressValid()` itself only checks length and prefix byte, accepting an all-zero payload address as "valid": [2](#0-1) 

This means a user can install a key entry whose address is the all-zero (burn-like) address into their own owner/active/witness permission with a non-trivial weight, exactly the class of bug described in the report (adding an unchecked "guardian"/privileged-list entry with the zero address).

The signature-weight logic that later consumes this list is `TransactionCapsule.checkWeight()`, which recovers a signer address from each provided signature via `SignUtils.signatureToAddress()` and looks it up against `permission.getKeysList()`: [3](#0-2) 
If a permission key list is allowed to contain the zero address (as it currently is), any code path where ECDSA/SM2 recovery could yield the zero address for a malformed/edge-case signature would incorrectly satisfy that key's weight requirement — mirroring the exact risk the Lido report warns about (an `ecrecover`-style zero result matching an unchecked zero-address guardian entry).

### Impact Explanation
If exploitable, this would let an account holder construct a permission set containing a zero-address key with a large weight, potentially allowing a crafted/invalid signature that resolves (via signature recovery) to the zero address to count toward the multisig threshold — undermining the integrity of that account's transaction authorization (unauthorized account operation class). At minimum, it results in a corrupted/nonsensical multisig configuration that a real Devin engineering task would need to validate against the concrete recovery behavior of `ECKey.signatureToAddress`/`SM2.signatureToAddress` (whether they can ever return an all-zero address for invalid input rather than throwing).

### Likelihood Explanation
Reaching `checkPermission()` requires only a normal, unprivileged `AccountPermissionUpdateContract` broadcast transaction from any account owner — no special privileges are needed to add the zero address to one's own permission list. However, full exploitation (using the zero-address key to satisfy weight without a real signer) depends on whether signature-recovery code (`ECKey`/`SM2` `signatureToAddress`) can actually resolve some invalid signature input to the zero address instead of throwing a `SignatureException`; this could not be fully confirmed from the available index and would need explicit verification of `ECKey.signatureToAddress`.

### Recommendation
Add an explicit zero-address rejection in `AccountPermissionUpdateActuator.checkPermission()` alongside the existing `DecodeUtil.addressValid()` check, e.g.:
```java
if (Arrays.equals(key.getAddress().toByteArray(), ZERO_ADDRESS)) {
  throw new ContractValidateException("key can not be zero address");
}
```
This closes off the possibility of a zero-address key ever appearing in a permission's key list, removing any dependency on downstream signature-recovery behavior.

### Proof of Concept
1. Broadcast an `AccountPermissionUpdateContract` transaction where one `Permission.Key` has `address = <prefixByte>0x00*20` and `weight > 0`.
2. `AccountPermissionUpdateActuator.validate()` calls `checkPermission()`, which only checks `DecodeUtil.addressValid()` (format-only) — the transaction passes validation and is executed. [4](#0-3) 
3. The account's permission now durably contains a zero-address key, which is subsequently consulted by `TransactionCapsule.checkWeight()`/`getWeight()` for every future signature check on that account.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L105-108)
```java
    for (Key key : permission.getKeysList()) {
      if (!DecodeUtil.addressValid(key.getAddress().toByteArray())) {
        throw new ContractValidateException("key is not a validate address");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L215-227)
```java
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

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L233-256)
```java
  public static long checkWeight(Permission permission, List<ByteString> sigs, byte[] hash,
      List<ByteString> approveList)
      throws SignatureException, PermissionException, SignatureFormatException {
    long currentWeight = 0;
    if (sigs.size() > permission.getKeysCount()) {
      throw new PermissionException(
          "Signature count is " + (sigs.size()) + " more than key counts of permission : "
              + permission.getKeysCount());
    }
    HashMap addMap = new HashMap();
    for (ByteString sig : sigs) {
      if (sig.size() < 65) {
        throw new SignatureFormatException(
            "Signature size is " + sig.size());
      }
      String base64 = TransactionCapsule.getBase64FromByteString(sig);
      byte[] address = SignUtils
          .signatureToAddress(hash, base64, CommonParameter.getInstance().isECKeyCryptoEngine());
      long weight = getWeight(permission, address);
      if (weight == 0) {
        throw new PermissionException(
            ByteArray.toHexString(hash) + " is signed by " + encode58Check(address)
                + " but it is not contained of permission.");
      }
```
