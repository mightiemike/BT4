### Title
Missing zero-address check allows irrecoverable loss of TRX/TRC10 in `TransferActuator` and `TransferAssetActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java`, `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java`)

### Summary
`TransferActuator.validate` and `TransferAssetActuator.validate` only verify the `toAddress` argument via `DecodeUtil.addressValid(toAddress)`, which checks length and the address prefix byte only, not whether the address body is the all-zero "zero address". Funds sent to a well-formed but all-zero address are permanently lost, exactly matching the reported bug class (missing `to != address(0)` check leading to fund loss).

### Finding Description
`DecodeUtil.addressValid` performs only two checks: that the byte array length equals `ADDRESS_SIZE / 2` (21 bytes) and that the first byte equals `addressPreFixByte` (`0x41`). [1](#0-0) 

It does not check whether the remaining 20 bytes of the address are all zero. Consequently, an address consisting of the valid prefix byte followed by 20 zero bytes (`0x41 0x00...0x00`) passes `addressValid` unconditionally.

`TransferActuator.validate` uses exactly this check for both `ownerAddress` and `toAddress`, and only additionally rejects the case where `toAddress` equals `ownerAddress` (self-transfer) — it never rejects the all-zero body address: [2](#0-1) 

`TransferActuator.execute` then unconditionally moves TRX to whatever `toAddress` was validated, creating the account if it doesn't exist yet: [3](#0-2) 

The same pattern exists in `TransferAssetActuator.validate`, which validates `toAddress` the same way and only additionally checks `Arrays.equals(ownerAddress, toAddress)`: [4](#0-3) 

and `execute` moves TRC10 asset balance to that address: [5](#0-4) 

This mirrors the reported issue class: any function accepting a user- or script-supplied `to` address but validating only its format (not its "meaningfulness") can transfer value to an unrecoverable, unowned address. In java-tron, since `addressValid` never rejects the zero-body address, any client, wallet, or exchange script that builds a `TransferContract`/`TransferAssetContract` with an uninitialized-but-prefixed 21-byte buffer (a common Java pattern: `byte[] addr = new byte[21]; addr[0] = prefix;`) will produce a transaction that is fully valid on-chain and irreversibly burns the sender's TRX/TRC10 tokens.

Other functions with `to`/receiver-style arguments in the codebase (e.g. `WithdrawBalanceActuator`, `WithdrawExpireUnfreezeActuator`) only credit the caller's own `ownerAddress` and have no externally supplied destination, so they are not affected by this bug class.

### Impact Explanation
If a transaction is broadcast with a `toAddress` equal to prefix+zero-body due to a client-side bug (e.g., unset/serialization default), the sending account's TRX (`TransferActuator`) or TRC10 asset balance (`TransferAssetActuator`) is transferred to that address and effectively becomes permanently inaccessible (no private key corresponds to an all-zero address body), constituting accounting/asset loss reachable from any unprivileged, anonymous broadcast transaction.

### Likelihood Explanation
Likelihood is moderate: the transaction itself requires no privilege and can be broadcast by anyone, but it requires the attacker/victim's own client tooling to accidentally (or intentionally, self-harmingly) construct a to-address with a zero body and a valid prefix byte — a class of bug more likely to occur in automated scripts (e.g., default-initialized byte buffers) than through manual wallet UI usage, similar to the exploit scenario described in the source report.

### Recommendation
Add an explicit check in `DecodeUtil.addressValid` (or separately in `TransferActuator.validate` and `TransferAssetActuator.validate`, and any other actuator that accepts a caller-supplied destination address) rejecting addresses whose 20-byte body is entirely zero, in addition to the existing length/prefix checks. Apply the same fix consistently to any other actuators/native-contract processors that accept a destination address parameter.

### Proof of Concept
1. Construct a `TransferContract` (or `TransferAssetContract`) with `ownerAddress` = a funded account and `toAddress` = `0x41` followed by 20 zero bytes (21 bytes total, valid length and prefix).
2. Sign and broadcast the transaction.
3. `TransferActuator.validate` (`actuator/src/main/java/org/tron/core/actuator/TransferActuator.java:100-113`) passes because `DecodeUtil.addressValid` only checks length/prefix, and `toAddress` differs from `ownerAddress` so the self-transfer check does not trigger.
4. `TransferActuator.execute` (`actuator/src/main/java/org/tron/core/actuator/TransferActuator.java:44-66`) creates the zero-body account (if absent) and credits it with the transferred amount, permanently locking the funds since no key can ever control that address.

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

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L44-66)
```java
      long amount = transferContract.getAmount();
      byte[] toAddress = transferContract.getToAddress().toByteArray();
      byte[] ownerAddress = transferContract.getOwnerAddress().toByteArray();

      // if account with to_address does not exist, create it first.
      AccountCapsule toAccount = accountStore.get(toAddress);
      if (toAccount == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccount = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccount);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }

      adjustBalance(accountStore, ownerAddress, -(addExact(fee, amount)));
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      adjustBalance(accountStore, toAddress, amount);
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

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L58-84)
```java
    try {
      TransferAssetContract transferAssetContract = this.any.unpack(TransferAssetContract.class);
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
      ByteString assetName = transferAssetContract.getAssetName();
      long amount = transferAssetContract.getAmount();

      AccountCapsule ownerAccountCapsule = accountStore.get(ownerAddress);
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
      accountStore.put(ownerAddress, ownerAccountCapsule);

      toAccountCapsule
          .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
      accountStore.put(toAddress, toAccountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L136-149)
```java
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }
    if (!DecodeUtil.addressValid(toAddress)) {
      throw new ContractValidateException("Invalid toAddress");
    }

    if (amount <= 0) {
      throw new ContractValidateException("Amount must be greater than 0.");
    }

    if (Arrays.equals(ownerAddress, toAddress)) {
      throw new ContractValidateException("Cannot transfer asset to yourself.");
    }
```
