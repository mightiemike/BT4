### Title
Token-ID leading-zero-byte mismatch between validation and balance mutation in `MUtil.transferToken` allows asset accounting corruption - (File: `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java`)

### Summary
`MUtil.transferToken(Repository, byte[], byte[], String, long)` validates the transfer using a token-id byte array with leading zero bytes stripped, but then performs the actual balance mutation using the original, un-stripped `tokenId.getBytes()`. An attacker who supplies a `tokenId` string containing a leading `\0` (or other leading zero byte) causes the check and the effect to operate on two different asset keys, breaking the balance invariant enforced by validation.

### Finding Description
`MUtil.transferToken` calls `VMUtils.validateForSmartContract(deposit, fromAddress, toAddress, tokenId.getBytes(), amount)` for validation: [1](#0-0) 

Inside `validateForSmartContract`, the token id used for all checks (asset existence, sender balance sufficiency, recipient overflow) is the **stripped** array: [2](#0-1) 

```java
byte[] tokenIdWithoutLeadingZero = ByteUtil.stripLeadingZeroes(tokenId);
...
if (deposit.getAssetIssue(tokenIdWithoutLeadingZero) == null) { ... }
Long assetBalance = ownerAccount.getAsset(deposit.getDynamicPropertiesStore(),
        ByteArray.toStr(tokenIdWithoutLeadingZero));
```

However, after validation passes, `MUtil.transferToken` performs the actual state mutation using the **original, un-stripped** `tokenId.getBytes()`:
```java
deposit.addTokenBalance(toAddress, tokenId.getBytes(), amount);
deposit.addTokenBalance(fromAddress, tokenId.getBytes(), -amount);
``` [3](#0-2) 

Because asset balances are keyed by the string form of the token id (`ByteArray.toStr(...)`), a `tokenId` value such as `"\u0000" + "1000001"` produces:
- Validation path: `stripLeadingZeroes` removes the leading `0x00` byte → checks are performed against the real token `"1000001"` (owner balance sufficiency, recipient overflow, asset existence) → passes.
- Mutation path: `tokenId.getBytes()` still contains the leading `0x00` byte → `addTokenBalance` operates on a distinct key `"\u00001000001"`, not `"1000001"`.

The net effect: the sender's real `"1000001"` balance is never decremented (validation confirmed sufficient balance for a token that is not actually debited), while a new/unrelated bogus-keyed asset entry is credited on the recipient and debited (potentially negative) on the sender. This desynchronizes the check from the effect, enabling value creation/corruption in the asset ledger that the intended balance check was supposed to prevent.

This is reachable by any smart-contract caller/deployer via the TVM opcode that triggers `MUtil.transferToken` (token transfer opcode in `VMActuator`/`Program`), since the `tokenId` supplied on the stack is attacker-controlled and converted to a `String` without canonicalizing leading zero bytes before being passed into `MUtil.transferToken`.

### Impact Explanation
This breaks the balance-sufficiency guarantee enforced by `validateForSmartContract`: the check is performed against one asset key while the mutation is applied to a different key. This can corrupt the accounting of TRC10 assets — effectively allowing the appearance of asset balance where the corresponding debit did not occur against the real token, and/or creating negative/garbage balances under a bogus key. This maps to the "asset/accounting corruption" impact class.

### Likelihood Explanation
Exploitability depends on confirming that the `tokenId` `String` reaching `MUtil.transferToken` can actually contain a raw `0x00` (or other zero) leading byte when it is derived from contract bytecode/stack input at the opcode level (e.g., via `Program.java`/`VMActuator.java`), which was not fully confirmed in this pass due to tool-call limits — the direct call site converting a stack `DataWord` into the `tokenId` string argument was not located. If the calling code canonicalizes/strips the token id before constructing the `String`, or restricts it to already-known asset id strings, this path would not be reachable by attacker-controlled leading zero bytes. This uncertainty should be verified against `Program.java`/`VMActuator.java` opcode handling for `TRANSFERTOKEN` before treating this as fully confirmed and exploitable.

### Recommendation
Use the same canonicalized token id (`tokenIdWithoutLeadingZero`, or otherwise normalize `tokenId` once at method entry) consistently in both the validation and mutation steps of `MUtil.transferToken`, e.g. pass the stripped byte array to `addTokenBalance` instead of `tokenId.getBytes()`.

### Proof of Concept
Not fully constructible without confirming the exact opcode-to-`MUtil.transferToken` call path (i.e., how the stack `DataWord` is converted into the `tokenId` `String` in `Program.java`/`VMActuator.java`), which could not be located within the available tool calls. A JUnit PoC would need to:
1. Deploy a contract that invokes the TVM `TRANSFERTOKEN`-equivalent opcode with a `tokenId` argument crafted to contain a leading zero byte followed by a valid existing asset id (e.g., bytes `0x00 '1' '0' '0' '0' '0' '0' '1'`).
2. Assert that `validateForSmartContract` passes (using stripped id "1000001").
3. Assert that after execution, the sender's `"1000001"` asset balance in `AccountCapsule` is unchanged, while a new asset entry keyed by the un-stripped string appears on the recipient (and/or negative on the sender) — confirming the mismatch.

This PoC could not be completed/verified within this session due to the tool-call limit reached before locating the opcode call site; further investigation of `Program.java` (TVM opcode dispatch) and `VMActuator.java` is required to confirm reachability of attacker-controlled leading-zero `tokenId` strings.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/utils/MUtil.java (L43-52)
```java
  public static void transferToken(Repository deposit, byte[] fromAddress, byte[] toAddress,
      String tokenId, long amount)
      throws ContractValidateException {
    if (0 == amount) {
      return;
    }
    VMUtils.validateForSmartContract(deposit, fromAddress, toAddress, tokenId.getBytes(), amount);
    deposit.addTokenBalance(toAddress, tokenId.getBytes(), amount);
    deposit.addTokenBalance(fromAddress, tokenId.getBytes(), -amount);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/VMUtils.java (L182-226)
```java
  public static boolean validateForSmartContract(Repository deposit, byte[] ownerAddress,
      byte[] toAddress, byte[] tokenId, long amount) throws ContractValidateException {
    if (deposit == null) {
      throw new ContractValidateException("No deposit!");
    }

    byte[] tokenIdWithoutLeadingZero = ByteUtil.stripLeadingZeroes(tokenId);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }
    if (!DecodeUtil.addressValid(toAddress)) {
      throw new ContractValidateException("Invalid toAddress");
    }

    if (amount <= 0) {
      throw new ContractValidateException("Amount must greater than 0.");
    }

    if (Arrays.equals(ownerAddress, toAddress)) {
      throw new ContractValidateException("Cannot transfer asset to yourself.");
    }

    AccountCapsule ownerAccount = deposit.getAccount(ownerAddress);
    if (ownerAccount == null) {
      throw new ContractValidateException("No owner account!");
    }

    if (deposit.getAssetIssue(tokenIdWithoutLeadingZero) == null) {
      throw new ContractValidateException("No asset !");
    }
    if (!Commons.getAssetIssueStoreFinal(deposit.getDynamicPropertiesStore(),
        deposit.getAssetIssueStore(), deposit.getAssetIssueV2Store())
        .has(tokenIdWithoutLeadingZero)) {
      throw new ContractValidateException("No asset !");
    }

    Long assetBalance = ownerAccount.getAsset(deposit.getDynamicPropertiesStore(),
            ByteArray.toStr(tokenIdWithoutLeadingZero));
    if (null == assetBalance || assetBalance <= 0) {
      throw new ContractValidateException("assetBalance must greater than 0.");
    }
    if (amount > assetBalance) {
      throw new ContractValidateException("assetBalance is not sufficient.");
    }
```
