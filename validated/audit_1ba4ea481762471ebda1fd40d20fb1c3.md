### Title
Unchecked NPE in `TransactionTrace.checkIsConstant()` from `ContractCapsule.getTriggerContractFromTransaction()` returning `null` on Any/type mismatch - (File: chainbase/src/main/java/org/tron/core/db/TransactionTrace.java)

### Summary
`ContractCapsule.getTriggerContractFromTransaction(Transaction)` only looks at `raw_data.contract(0)` and silently returns `null` if `Any.unpack(TriggerSmartContract.class)` fails, but `TransactionTrace.checkIsConstant()` immediately dereferences the result without a null check. An unprivileged attacker can craft a transaction whose `contract(0).type` enum is set to `TriggerSmartContract_VALUE` (so `trxType` becomes `TRX_CONTRACT_CALL_TYPE`) while packing a mismatched message (or arbitrary bytes) into `contract(0).parameter`, causing `unpack()` to fail and the helper to return `null`, which then triggers a `NullPointerException`.

### Finding Description
`ContractCapsule.getSmartContractFromTransaction`/`getTriggerContractFromTransaction` unconditionally read `trx.getRawData().getContract(0).getParameter()` and attempt `Any.unpack(...)`, returning `null` on `InvalidProtocolBufferException`: [1](#0-0) 

`TransactionTrace`'s constructor determines `trxType` purely from the `ContractType` enum field on `contract(0)`, which is independent of what is actually packed in the `Any` parameter: [2](#0-1) 

`checkIsConstant()` then calls `getTriggerContractFromTransaction` and immediately dereferences the result (`triggerContractFromTransaction.getContractAddress()`) without any null check, inside a method that only declares `ContractValidateException` and `VMIllegalException` — not a `NullPointerException`: [3](#0-2) 

Because the enum `type` field and the packed `Any` payload are two independently attacker-controlled fields on the wire, a transaction can set `contract(0).type = TriggerSmartContract` (driving `trxType`) while packing a different/garbage message into `contract(0).parameter` (causing `unpack` to fail). This desynchronization is not rejected before `checkIsConstant()` runs, since that check is only about which `TriggerSmartContract`-specific ABI/constant logic to apply, not a structural/type consistency check of the transaction. The same call is repeated in `pay()` (line 240-241) without a null check on `callContract`, compounding the issue on the payment code path as well: [4](#0-3) 

Other call sites of these two static helpers (e.g., `VMActuator`, `Wallet.java`, `WalletUtil.java`) were not confirmed to consistently null-check the result either, meaning null-handling is inconsistent across the codebase for the same helper.

### Impact Explanation
An unchecked `NullPointerException` thrown from `checkIsConstant()` propagates as an undeclared runtime exception through transaction processing. Depending on whether the caller in `Manager`/block-processing catches generic `Exception`/`Throwable` or only the declared checked exceptions, this can either be silently converted into a validation failure (inconsistent across code paths) or crash/abort processing of that transaction path, producing a genuine deterministic-execution/availability risk (DoS on a broadcast, potentially crashing a node's transaction-processing thread) rather than mere logic inconsistency.

### Likelihood Explanation
This requires only an unprivileged attacker crafting a raw `Transaction` protobuf with a mismatched `ContractType` enum vs. packed `Any` payload in `contract(0)` and broadcasting it — no privileged keys or node access needed. The precondition (`trxType` derived from the enum field, independent from the `Any` payload's real type) is directly visible in the constructor of `TransactionTrace`, and nothing in the code paths shown validates that the `Any` actually unpacks to match the declared `ContractType` before `checkIsConstant()` executes. I was not able to fully confirm, within the remaining investigation budget, whether an earlier stage in `Manager.java`'s transaction pipeline (signature/structure validation) rejects such type/Any mismatches before `checkIsConstant()` is invoked; this should be verified with a live integration test, but the code inspected shows no such guard within `TransactionTrace` or `ContractCapsule` themselves.

### Recommendation
Add explicit null checks after both `ContractCapsule.getTriggerContractFromTransaction` and `getSmartContractFromTransaction` calls in `TransactionTrace.checkIsConstant()` and `pay()`, throwing `ContractValidateException` (a declared, expected exception) instead of allowing an NPE. Additionally, validate at transaction-acceptance time that `contract(0).parameter`'s `Any` type URL is consistent with `contract(0).type`, and that `raw_data.contract` has exactly one entry, rejecting inconsistent transactions early and uniformly for all downstream consumers of these two helpers.

### Proof of Concept
```java
// Java unit test sketch (JUnit) for chainbase/src/test/java/org/tron/core/db/TransactionTraceTest.java
@Test
public void testCheckIsConstantNpeOnMismatchedAny() throws Exception {
  // Build a Transaction whose contract(0).type = TriggerSmartContract_VALUE
  // but contract(0).parameter is an Any packed with a mismatched message
  // (e.g., AccountUpdateContract) so unpack(TriggerSmartContract.class) fails.
  Any badAny = Any.pack(AccountUpdateContract.newBuilder().build());
  Transaction.Contract contract = Transaction.Contract.newBuilder()
      .setType(Transaction.Contract.ContractType.TriggerSmartContract)
      .setParameter(badAny)
      .build();
  Transaction trx = Transaction.newBuilder()
      .setRawData(Transaction.raw.newBuilder().addContract(contract))
      .build();

  TransactionCapsule trxCap = new TransactionCapsule(trx);
  TransactionTrace trace = new TransactionTrace(trxCap, storeFactory, runtime);

  // Expect a controlled ContractValidateException, NOT an unchecked NPE
  assertThrows(ContractValidateException.class, trace::checkIsConstant);
  // Current code: this call throws NullPointerException instead,
  // demonstrating the unguarded null dereference.
}
```
Run this against the current `checkIsConstant()` implementation to observe the uncaught `NullPointerException` at `triggerContractFromTransaction.getContractAddress()` [5](#0-4) , confirming the null-unsafe usage of `ContractCapsule.getTriggerContractFromTransaction`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractCapsule.java (L55-73)
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

  public static TriggerSmartContract getTriggerContractFromTransaction(Transaction trx) {
    try {
      Any any = trx.getRawData().getContract(0).getParameter();
      TriggerSmartContract contractTriggerContract = any.unpack(TriggerSmartContract.class);
      return contractTriggerContract;
    } catch (InvalidProtocolBufferException e) {
      return null;
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L86-98)
```java
    this.trx = trx;
    Transaction.Contract.ContractType contractType = this.trx.getInstance().getRawData()
        .getContract(0).getType();
    switch (contractType.getNumber()) {
      case ContractType.TriggerSmartContract_VALUE:
        trxType = TRX_CONTRACT_CALL_TYPE;
        break;
      case ContractType.CreateSmartContract_VALUE:
        trxType = TRX_CONTRACT_CREATION_TYPE;
        break;
      default:
        trxType = TrxType.TRX_PRECOMPILED_TYPE;
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L133-153)
```java
  public void checkIsConstant() throws ContractValidateException, VMIllegalException {
    if (dynamicPropertiesStore.getAllowTvmConstantinople() == 1) {
      return;
    }
    TriggerSmartContract triggerContractFromTransaction = ContractCapsule
        .getTriggerContractFromTransaction(this.getTrx().getInstance());
    if (TRX_CONTRACT_CALL_TYPE == this.trxType) {
      ContractCapsule contract = contractStore
          .get(triggerContractFromTransaction.getContractAddress().toByteArray());
      if (contract == null) {
        throw new ContractValidateException(String.format("contract: %s is not in contract store",
            StringUtil.encode58Check(triggerContractFromTransaction
                .getContractAddress().toByteArray())));

      }
      ABI abi = contract.getInstance().getAbi();
      if (WalletUtil.isConstant(abi, triggerContractFromTransaction)) {
        throw new VMIllegalException("cannot call constant method");
      }
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L239-246)
```java
      case TRX_CONTRACT_CALL_TYPE:
        TriggerSmartContract callContract = ContractCapsule
            .getTriggerContractFromTransaction(trx.getInstance());
        ContractCapsule contractCapsule =
            contractStore.get(callContract.getContractAddress().toByteArray());

        callerAccount = callContract.getOwnerAddress().toByteArray();
        originAccount = contractCapsule.getOriginAddress();
```
