### Title
Unbounded `Contract.getTypeValue()` causes uncaught `IndexOutOfBoundsException` in `checkPermissionOperations`, crashing transaction validation - ([File: chainbase/src/main/java/org/tron/common/utils/WalletUtil.java], [File: actuator/src/main/java/org/tron/core/utils/TransactionUtil.java])

### Summary
Both `WalletUtil.checkPermissionOperations` and `TransactionUtil.checkPermissionOperations` compute `contract.getTypeValue() / 8` as an index into a fixed 32-byte `operations` bitmap without validating that the value is within `[0, 255]`. Since `getTypeValue()` returns the raw protobuf enum tag (an arbitrary `int32` that can exceed the defined `ContractType` enum range, including unknown/reserved tags preserved on the wire), an attacker-supplied contract type can drive `byteAt()` out of bounds and throw an uncaught `IndexOutOfBoundsException`.

### Finding Description
`WalletUtil.checkPermissionOperations` [1](#0-0)  and the near-identical `TransactionUtil.checkPermissionOperations` [2](#0-1)  both validate only that `operations.size() == 32` and then compute:
```
int contractType = contract.getTypeValue();
boolean b = (operations.byteAt(contractType / 8) & (1 << (contractType % 8))) != 0;
```
`getTypeValue()` returns the raw protobuf enum integer as placed on the wire by the sender, which is not restricted to the values enumerated in `Protocol.Transaction.ContractType` — protobuf preserves unknown enum values in scalar (non-message) enum fields. Because `Contract.type` is a plain enum field (not `repeated`/oneof with unknown-field fallback removal), a crafted transaction can set an arbitrary 32-bit value for `type`, causing `contractType / 8` to index far outside the 32-byte `operations` `ByteString`, and `byteAt()` throws `IndexOutOfBoundsException` (also possible for negative values under two's-complement type tags).

`TransactionCapsule` imports and calls `WalletUtil.checkPermissionOperations` as part of permission checking during signature validation [3](#0-2) , which is on the path invoked from `validatePubSignature`/`checkPermission` during `Manager.pushTransaction`. `TransactionUtil.checkPermissionOperations` is likewise called for `permissionId != 0` transactions in `getTransactionSignWeight`, but there it is wrapped in a broad `try { ... } catch (Exception ex)` block that would actually catch the `IndexOutOfBoundsException` [4](#0-3) . However, the `WalletUtil` variant used inside `TransactionCapsule`'s signature/permission validation path is not shown to have such a catch-all around it, so an uncaught `IndexOutOfBoundsException` there would propagate up through `validatePubSignature` into `Manager.pushTransaction`, since that method (per the question) only catches `SignatureException`, `PermissionException`, and `SignatureFormatException`.

### Impact Explanation
An uncaught `RuntimeException` (`IndexOutOfBoundsException`) thrown from deep in transaction processing (`pushTransaction`) can crash or destabilize the block-producing/tx-validation thread, causing denial of service for the node processing the crafted transaction. If this occurs during block application (not just mempool admission), it could halt block production/validation for that node.

### Likelihood Explanation
The attack requires no privileged access: any account with `permissionId != 0` (an Active permission, which unprivileged users can set up on their own accounts via `AccountPermissionUpdateActuator`) can broadcast a raw `Transaction` object with `permissionId != 0` and a `Contract.type` field carrying an out-of-range enum tag via gRPC/HTTP `broadcastTransaction`. This is fully within an attacker's control since protobuf allows arbitrary enum tag values to be set on the wire for a scalar enum field, and the code does not clamp or validate `contractType` before using it as an array index.

### Recommendation
In both `WalletUtil.checkPermissionOperations` and `TransactionUtil.checkPermissionOperations`, validate `contractType` is within `[0, 255]` (i.e., `contractType >= 0 && contractType / 8 < operations.size()`) before indexing, throwing a `PermissionException` for out-of-range values instead of allowing an unchecked array-index exception to escape. Additionally, ensure any call sites (e.g., inside `TransactionCapsule`'s permission-check path used by `validatePubSignature`) wrap the checked exceptions properly so unexpected runtime exceptions cannot escape into `Manager.pushTransaction`.

### Proof of Concept
```java
@Test
public void testCheckPermissionOperations_outOfRangeContractType() {
  Permission permission = Permission.newBuilder()
      .setOperations(ByteString.copyFrom(new byte[32]))
      .build();

  Transaction.Contract contract = Transaction.Contract.newBuilder()
      .setTypeValue(9999) // out-of-range / unknown enum tag
      .build();

  // Expect a PermissionException, NOT an unchecked IndexOutOfBoundsException
  assertThrows(PermissionException.class,
      () -> WalletUtil.checkPermissionOperations(permission, contract));
}
```
Fuzz variant: feed `setTypeValue` values from `256` to `Integer.MAX_VALUE` (and negative values) into `checkPermissionOperations` and assert every call either returns a boolean or throws `PermissionException`, never `IndexOutOfBoundsException`.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L27-37)
```java
  public static boolean checkPermissionOperations(Permission permission, Contract contract)
      throws PermissionException {
    ByteString operations = permission.getOperations();
    if (operations.size() != 32) {
      throw new PermissionException(String.format("operations size must 32, actual: %d",
          operations.size()));
    }
    int contractType = contract.getTypeValue();
    boolean b = (operations.byteAt(contractType / 8) & (1 << (contractType % 8))) != 0;
    return b;
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L171-180)
```java
  public static boolean checkPermissionOperations(Permission permission, Contract contract)
      throws PermissionException {
    ByteString operations = permission.getOperations();
    if (operations.size() != 32) {
      throw new PermissionException("operations size must be 32");
    }
    int contractType = contract.getTypeValue();
    boolean b = (operations.byteAt(contractType / 8) & (1 << (contractType % 8))) != 0;
    return b;
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L224-271)
```java
      try {
        Contract contract = trx.getRawData().getContract(0);
        byte[] owner = TransactionCapsule.getOwner(contract);
        AccountCapsule account = chainBaseManager.getAccountStore().get(owner);
        if (Objects.isNull(account)) {
          throw new PermissionException("Account does not exist!");
        }
        int permissionId = contract.getPermissionId();
        Permission permission = account.getPermissionById(permissionId);
        if (permission == null) {
          throw new PermissionException("Permission for this, does not exist!");
        }
        if (permissionId != 0) {
          if (permission.getType() != PermissionType.Active) {
            throw new PermissionException("Permission type is wrong!");
          }
          //check operations
          if (!checkPermissionOperations(permission, contract)) {
            throw new PermissionException("Permission denied!");
          }
        }
        tswBuilder.setPermission(permission);
        if (trx.getSignatureCount() > 0) {
          List<ByteString> approveList = new ArrayList<>();
          long currentWeight = TransactionCapsule.checkWeight(permission, trx.getSignatureList(),
              Sha256Hash.hash(CommonParameter.getInstance()
                  .isECKeyCryptoEngine(), trx.getRawData().toByteArray()), approveList);
          tswBuilder.addAllApprovedList(approveList);
          tswBuilder.setCurrentWeight(currentWeight);
        }
        if (tswBuilder.getCurrentWeight() >= permission.getThreshold()) {
          resultBuilder.setCode(Result.response_code.ENOUGH_PERMISSION);
        } else {
          resultBuilder.setCode(Result.response_code.NOT_ENOUGH_PERMISSION);
        }
      } catch (SignatureFormatException signEx) {
        resultBuilder.setCode(Result.response_code.SIGNATURE_FORMAT_ERROR);
        resultBuilder.setMessage(signEx.getMessage());
      } catch (SignatureException signEx) {
        resultBuilder.setCode(Result.response_code.COMPUTE_ADDRESS_ERROR);
        resultBuilder.setMessage(signEx.getMessage());
      } catch (PermissionException permEx) {
        resultBuilder.setCode(Result.response_code.PERMISSION_ERROR);
        resultBuilder.setMessage(permEx.getMessage());
      } catch (Exception ex) {
        resultBuilder.setCode(Result.response_code.OTHER_ERROR);
        resultBuilder.setMessage(ex.getClass() + " : " + ex.getMessage());
      }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L18-19)
```java
import static org.tron.common.utils.StringUtil.encode58Check;
import static org.tron.common.utils.WalletUtil.checkPermissionOperations;
```
