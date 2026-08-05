## Title
Missing zero-address validation in TRX/TRC10 transfer actuators allows irrecoverable loss of funds - (File: `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java`, `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java`)

### Summary
`TransferActuator.validate()` and `TransferAssetActuator.validate()` only verify that `toAddress` is well-formed (correct length and network prefix byte) via `DecodeUtil.addressValid()`. Neither function rejects a syntactically "valid" but semantically null destination — an address consisting of the network prefix byte followed by 20 zero bytes. This mirrors the reported bug class (missing zero-address check on a `to` parameter that can be transferred funds), where an uninitialized/defaulted `to` value causes an irrecoverable loss of funds.

### Finding Description
`DecodeUtil.addressValid()` checks only that the address is non-empty, exactly 21 bytes long, and starts with `addressPreFixByte` (`0x41` on mainnet); it performs no check against the zero-payload address (`0x41` + 20 zero bytes): [1](#0-0) 

`TransferActuator.validate()` uses exactly this check on `toAddress` before permitting the balance transfer, with no additional guard against the zero-payload address: [2](#0-1) 

Similarly, `TransferAssetActuator.validate()` validates `toAddress` the same way and then, in `execute()`, will transparently create a fresh `AccountCapsule` at that (zero-payload) address and credit the asset amount to it if none exists yet: [3](#0-2) [4](#0-3) 

Because TRON addresses are derived from ECDSA public keys, an address whose 20-byte payload is all zeros has a negligible probability of a corresponding known private key, so any TRX or TRC10 value sent there is effectively unspendable/permanently lost — the same "funds sent to zero address" outcome described in the external report for `VaultFactory.claimFees` / `LoanCore.withdraw*`.

### Impact Explanation
Any unprivileged account holder who submits a `TransferContract` or `TransferAssetContract` with `to_address` defaulted/left as the zero-payload value (e.g. due to a wallet/SDK bug, uninitialized byte array, or a batch/script error analogous to the exploit scenario in the report) will have that transaction validated and executed successfully, permanently locking the transferred TRX or TRC10 balance at an address nobody controls. This is a concrete, reachable state/accounting impact: value is irreversibly removed from circulation for that account with no recovery path, matching the "loss of funds" impact class from the report.

### Likelihood Explanation
The path is reachable by any unprivileged user through the standard `TransferContract`/`TransferAssetContract` broadcast flow with no special privilege required. Likelihood of *triggering* it is tied to client-side bugs (uninitialized/default `to_address`) rather than a direct third-party attack, consistent with the original report's own exploit scenario (a faulty script leaving the `to` argument at its default/zero value).

### Recommendation
Add an explicit check in `TransferActuator.validate()` and `TransferAssetActuator.validate()` (and any other actuator that accepts a caller-supplied destination address for value transfer) rejecting `toAddress` when its 20-byte payload is all zero, in addition to the existing `DecodeUtil.addressValid()` format check. Consider centralizing this as a helper (e.g., `DecodeUtil.isZeroAddress()`) reused across all transfer/resource-delegation actuators.

### Proof of Concept
1. Construct a `TransferContract` (or `TransferAssetContract`) with `owner_address` = a funded account and `to_address` = `0x41` followed by 20 zero bytes (21 bytes total, matching `ADDRESS_SIZE`).
2. Submit the transaction; `DecodeUtil.addressValid(toAddress)` returns `true` because it only checks length and prefix byte, so `validate()` passes. [5](#0-4) 
3. `execute()` credits the zero-payload address's balance and debits the sender; the transaction succeeds with `code.SUCESS`.
4. The transferred TRX/TRC10 balance is now held by an address with no known private key, permanently unspendable.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L100-113)
```java
    byte[] toAddress = transferContract.getToAddress().toByteArray();
    byte[] ownerAddress = transferContract.getOwnerAddress().toByteArray();
    long amount = transferContract.getAmount();

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress!");
    }
    if (!DecodeUtil.addressValid(toAddress)) {
      throw new ContractValidateException("Invalid toAddress!");
    }

    if (Arrays.equals(toAddress, ownerAddress)) {
      throw new ContractValidateException("Cannot transfer TRX to yourself.");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L60-71)
```java
      byte[] ownerAddress = transferAssetContract.getOwnerAddress().toByteArray();
      byte[] toAddress = transferAssetContract.getToAddress().toByteArray();
      AccountCapsule toAccountCapsule = accountStore.get(toAddress);
      if (toAccountCapsule == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccountCapsule = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccountCapsule);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L136-141)
```java
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }
    if (!DecodeUtil.addressValid(toAddress)) {
      throw new ContractValidateException("Invalid toAddress");
    }
```
