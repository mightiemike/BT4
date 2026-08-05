### Title
NullPointerException via malformed `Any` payload in `ContractCapsule.getSmartContractFromTransaction` unchecked by `WalletUtil.generateContractAddress` - (File: `ContractCapsule.java` / `WalletUtil.java`)

### Summary
`ContractCapsule.getSmartContractFromTransaction(Transaction)` swallows `InvalidProtocolBufferException` from `any.unpack(CreateSmartContract.class)` and returns `null` on any malformed/truncated `Any.value` payload. `WalletUtil.generateContractAddress(Transaction)` calls this method and immediately dereferences the result with `contract.getOwnerAddress()` without a null check, giving an attacker-controlled crash path.

### Finding Description
`getSmartContractFromTransaction` unpacks `trx.getRawData().getContract(0).getParameter()` and returns `null` on `InvalidProtocolBufferException`: [1](#0-0) 

`WalletUtil.generateContractAddress` calls this and dereferences the result unconditionally: [2](#0-1) 

The `Any` protobuf's `is(Class)`/`unpack(Class)` semantics only validate the `type_url` string against the target message's descriptor full name; they do not validate that the `value` bytes are well-formed protobuf for that type. An attacker can therefore construct a `CreateSmartContract` transaction whose `Contract.parameter` has a correctly matching `type_url` (`type.googleapis.com/protocol.CreateSmartContract`) but truncated/corrupted `value` bytes. Any upstream `is()`-style pre-check (if present in the actuator's `validate()`) would pass since it only compares `type_url`, but the subsequent `unpack()` call inside `getSmartContractFromTransaction` still throws `InvalidProtocolBufferException` due to malformed bytes, causing the method to return `null`. If `generateContractAddress` (or any other unchecked caller) is reached with this `null`, `contract.getOwnerAddress()` throws an unhandled `NullPointerException`.

### Impact Explanation
An unhandled `NullPointerException` during transaction/contract-address processing on a path that is not defensively try/caught can propagate up through block/transaction processing, potentially halting processing of that transaction path or crashing the node process depending on where the exception surfaces in the call stack (block validation vs. isolated actuator execution). At minimum this is a deterministic-execution violation: a syntactically valid broadcastable transaction causes an unhandled exception instead of a graceful `ContractValidateException`.

### Likelihood Explanation
Feasibility depends entirely on whether `WalletUtil.generateContractAddress` (or other unchecked callers of `getSmartContractFromTransaction`/`getTriggerContractFromTransaction`) can be reached with a transaction whose `Any.type_url` matches but whose `value` bytes are corrupted, i.e., whether any upstream validator performs a full deserialization check (which would already throw a handled `ContractValidateException`) or only a `type_url`/`is()` check before this code path executes. I could not fully trace the actuator `validate()` chain (e.g., `CreateSmartContractActuator`) that gates the call to `generateContractAddress` within the available context, so I cannot confirm with certainty that no earlier guard rejects the malformed payload before reaching the unchecked dereference. This is a real code-level gap, but full end-to-end reachability from an unauthenticated broadcast transaction should be verified with a live trace/PoC.

### Recommendation
Make `getSmartContractFromTransaction`/`getTriggerContractFromTransaction` callers null-safe: check for `null` immediately after calling these methods in `WalletUtil.generateContractAddress` and any other caller (`VMActuator`, `ProgramInvokeFactory`, `TransactionTrace`, `Wallet.java`), throwing a `ContractValidateException`/`ContractExeException` instead of allowing an NPE, or change the capsule methods to throw a checked exception that all call sites must handle.

### Proof of Concept
```java
// Java unit test (JUnit) sketch for chainbase module
@Test
public void testGenerateContractAddressWithMalformedAny() {
  Any malformedAny = Any.newBuilder()
      .setTypeUrl("type.googleapis.com/protocol.CreateSmartContract") // matches is() check
      .setValue(ByteString.copyFrom(new byte[]{0x7f, 0x00, 0x01})) // truncated/invalid bytes
      .build();

  Transaction.Contract contract = Transaction.Contract.newBuilder()
      .setType(Transaction.Contract.ContractType.CreateSmartContract)
      .setParameter(malformedAny)
      .build();

  Transaction trx = Transaction.newBuilder()
      .setRawData(Transaction.raw.newBuilder().addContract(contract).build())
      .build();

  // getSmartContractFromTransaction should return null
  assertNull(ContractCapsule.getSmartContractFromTransaction(trx));

  // This call is expected to throw NullPointerException, demonstrating the crash path
  assertThrows(NullPointerException.class,
      () -> WalletUtil.generateContractAddress(trx));
}
```
Expected assertion after fix: `WalletUtil.generateContractAddress` (and other callers) should throw a handled `ContractValidateException`/return an error result instead of an unhandled `NullPointerException`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractCapsule.java (L55-63)
```java
  public static CreateSmartContract getSmartContractFromTransaction(Transaction trx) {
    try {
      Any any = trx.getRawData().getContract(0).getParameter();
      CreateSmartContract createSmartContract = any.unpack(CreateSmartContract.class);
      return createSmartContract;
    } catch (InvalidProtocolBufferException e) {
      return null;
    }
  }
```

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L39-52)
```java
  public static byte[] generateContractAddress(Transaction trx) {

    CreateSmartContract contract = ContractCapsule.getSmartContractFromTransaction(trx);
    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    TransactionCapsule trxCap = new TransactionCapsule(trx);
    byte[] txRawDataHash = trxCap.getTransactionId().getBytes();

    byte[] combined = new byte[txRawDataHash.length + ownerAddress.length];
    System.arraycopy(txRawDataHash, 0, combined, 0, txRawDataHash.length);
    System.arraycopy(ownerAddress, 0, combined, txRawDataHash.length, ownerAddress.length);

    return Hash.sha3omit12(combined);

  }
```
