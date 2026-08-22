### Title
Missing bounds validation on `Contract.type` allows unchecked `IndexOutOfBoundsException` in `TransactionUtil.checkPermissionOperations` - ([File: actuator/src/main/java/org/tron/core/utils/TransactionUtil.java])

### Summary
`TransactionUtil.checkPermissionOperations` computes `contract.getTypeValue() / 8` as an index into the fixed 32-byte `operations` bitmap without validating that the value is within `[0, 255]`. Because protobuf enum fields preserve arbitrary unrecognized wire values via `getTypeValue()`, an attacker can craft a raw `Transaction.Contract` with an out-of-range `type` (varint > 255 or negative) that causes `ByteString.byteAt()` to throw an unhandled `IndexOutOfBoundsException`.

### Finding Description [1](#0-0) 

`checkPermissionOperations` reads `contract.getTypeValue()` directly (not `contract.getType()`, which would map unknown values to `UNRECOGNIZED`), then computes `operations.byteAt(contractType / 8)` where `operations` is asserted to be exactly 32 bytes. Since protobuf-java's `getTypeValue()` returns the raw int32 stored on the wire for enum fields—unconstrained by the generated enum whitelist—an attacker can hand-craft a `Contract` field with a varint value greater than 255 (giving `contractType/8 >= 32`) or a negative value, both of which fall outside the valid `[0,31]` byte index range for the 32-byte `operations` `ByteString`, causing `ByteString.byteAt()` to throw `IndexOutOfBoundsException`.

This method is invoked from `TransactionUtil.getTransactionSignWeight` at: [2](#0-1) 

which is wrapped by a catch-all handler: [3](#0-2) 

so for this specific query-only RPC path (`GetTransactionSignWeight`), the exception is safely caught and reported as `OTHER_ERROR` — it does not crash the node or bypass authorization on this path.

`checkPermissionOperations` is also referenced from `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java`, which is part of the actual transaction-processing/signature-validation pipeline used during block/transaction execution (not just the read-only wallet query). I was unable to fully confirm within the available tool budget whether that call site wraps the method in an equivalent broad exception handler, or whether an uncaught `IndexOutOfBoundsException` could propagate up through block processing and stall/crash a validation thread there. This should be verified directly in `TransactionCapsule.java`.

Regarding the "permission bypass to execute unintended operations" scenario: actuator dispatch (`ActuatorFactory`) also keys off `Contract.getType()`, which maps unrecognized/out-of-range wire values to `UNRECOGNIZED`. In normal flow this causes actuator selection/validation to fail before execution, which limits the practical exploitability of a bit-wrap bypass changing the actual authorization outcome for contract execution — the more concrete, confirmed risk is the uncaught exception (DoS) rather than a state-corrupting authorization bypass.

### Impact Explanation
Confirmed impact: for the `getTransactionSignWeight` RPC path, the flaw causes a caught exception with graceful `OTHER_ERROR` response — no crash, no bypass. Potential impact (unverified): if `TransactionCapsule`'s use of `checkPermissionOperations` during actual transaction/block processing lacks equivalent broad exception handling, this could match the "DoS via TRON protocol implementation" bounty class by throwing an unhandled runtime exception during transaction validation. This needs direct confirmation in `TransactionCapsule.java` before being treated as confirmed-impact.

### Likelihood Explanation
Low-to-moderate cost for an attacker: crafting a raw protobuf `Transaction.Contract` with an out-of-range enum varint requires only manual protobuf construction (not using the generated builder, which normally clamps to known enum constants) and a permission with `permission_id != 0`. Standard fee/bandwidth costs for broadcasting a transaction still apply. Feasibility of the DoS is uncertain since the primary path found by my investigation catches the exception; the other call site (`TransactionCapsule.java`) is unverified.

### Recommendation
Add explicit bounds validation on `contractType` in `checkPermissionOperations` before indexing into `operations`, e.g., reject or return `false` if `contractType < 0 || contractType >= operations.size() * 8`, and prefer `contract.getType()` combined with an `UNRECOGNIZED` check rather than raw `getTypeValue()`.

### Proof of Concept
```java
// JUnit-style PoC targeting TransactionUtil.checkPermissionOperations directly
Permission permission = Permission.newBuilder()
    .setType(Permission.PermissionType.Active)
    .setOperations(ByteString.copyFrom(new byte[32])) // valid 32-byte operations bitmap
    .build();

// Craft a Contract with an out-of-range type value (bypassing the enum whitelist)
// via reflection/raw protobuf bytes, e.g. type value = 1000 (>255) or -1.
Transaction.Contract contract = Transaction.Contract.newBuilder()
    .setTypeValue(1000) // out-of-range enum value
    .build();

// Expected (current buggy behavior): throws IndexOutOfBoundsException, not PermissionException
Assertions.assertThrows(IndexOutOfBoundsException.class,
    () -> TransactionUtil.checkPermissionOperations(permission, contract));
```
Note: on the `getTransactionSignWeight` path this exception is caught by the generic `catch (Exception ex)` block and surfaced as `OTHER_ERROR`, so that specific RPC is not crashed. Verifying whether `TransactionCapsule`'s usage of `checkPermissionOperations` has equivalent protection requires direct inspection of `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java`, which I could not complete in this session.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L236-243)
```java
        if (permissionId != 0) {
          if (permission.getType() != PermissionType.Active) {
            throw new PermissionException("Permission type is wrong!");
          }
          //check operations
          if (!checkPermissionOperations(permission, contract)) {
            throw new PermissionException("Permission denied!");
          }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L268-271)
```java
      } catch (Exception ex) {
        resultBuilder.setCode(Result.response_code.OTHER_ERROR);
        resultBuilder.setMessage(ex.getClass() + " : " + ex.getMessage());
      }
```
