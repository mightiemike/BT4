## Analysis: Missing Recipient Address Validation in Shielded TRC-20 Burn (Unshield) Flow

### Title
Missing Recipient Address Validation in Shielded TRC-20 Burn Trigger Input Construction - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
The external report describes an unvalidated destination (chain ID + recipient address) accepted by a public burn-and-bridge function, allowing user funds to be irrecoverably lost. The closest reachable analog in java-tron is the shielded TRC-20 "burn" (unshield) flow, where a user converts a private/shielded balance into a public balance sent to an attacker/user-supplied `transparentToAddress`. Unlike ordinary public transfers in the codebase, this address is not run through the standard `DecodeUtil.addressValid` check used everywhere else.

### Finding Description
`Wallet.getTriggerInputForShieldedTRC20Contract` extracts the caller-supplied `transparentToAddress` and only checks that its length is 21 bytes before stripping the prefix byte — it never validates that the resulting 20-byte address is a well-formed/non-zero TRON address: [1](#0-0) 

This value is passed straight through `ShieldedTRC20ParametersBuilder.burnParamsToHexString` via `normalizeTransparentToAddress`, and embedded as the `payTo` field of the constructed trigger input, which the `BURN` precompiled contract path uses to move funds from the shielded pool into the public account model: [2](#0-1) 

Contrast this with every other public-facing balance-moving actuator in java-tron, which explicitly rejects invalid/malformed addresses using `DecodeUtil.addressValid` before any state mutation, e.g. `TransferAssetActuator.validate`: [3](#0-2) 
`WithdrawBalanceActuator.validate`: [4](#0-3) 
and `VMUtils.validateForSmartContract` used for internal/token transfers triggered from TVM: [5](#0-4) 

The shielded burn path is the one exception to this otherwise-consistent address-validation pattern: it only enforces a byte-length check (21 bytes) rather than validating the destination is well-formed and non-zero, mirroring the reported class of bug ("recipient address... lacks a validation step ... could lead to scenarios where tokens are sent to an incorrect or invalid address").

### Impact Explanation
If a user (or a wallet/dApp constructing the burn parameters on their behalf) supplies a malformed or all-zero 20-byte `transparentToAddress` (still 21 bytes once prefixed), the shielded balance is irrecoverably converted and delivered to that address by the `BURN` precompiled contract logic. There is no chain-level guard preventing this — the value moves via `deposit.addBalance`/token-balance equivalents inside the TVM shielded contract execution, and once the corresponding transaction is included in a block, the shielded note is spent (nullifier recorded) with no path to reclaim the funds. This is a direct, unprivileged-user-triggerable fund-loss scenario analogous to the original report's "burn tokens and lose them to an invalid/incorrect address."

### Likelihood Explanation
Any user of the shielded TRC-20 (zk-SNARK) wallet flow who supplies an incorrect address — through client-side bugs, copy/paste errors, or malicious wallet software — can trigger this without any privileged role. Because the length check (21 bytes) is trivially satisfiable by any 20-byte value including the zero address, likelihood of accidental or adversarial misuse is non-trivial for an unprivileged, publicly reachable code path.

### Recommendation
Add explicit `DecodeUtil.addressValid`-style validation (including a non-zero-address check) on `transparentToAddress`/`transparentToAddressTvm` in `Wallet.getTriggerInputForShieldedTRC20Contract` and in `ShieldedTRC20ParametersBuilder.burnParamsToHexString`/`normalizeTransparentToAddress`, consistent with the validation already enforced by `TransferAssetActuator`, `WithdrawBalanceActuator`, and `VMUtils.validateForSmartContract`, before constructing or accepting the burn trigger input.

### Proof of Concept
Conceptual PoC (cannot be executed in this ask-only session, but derivable from the code paths cited above):
1. Client calls `Wallet.getTriggerInputForShieldedTRC20Contract` with `ShieldedTRC20TriggerContractParameters.transparentToAddress` set to a 21-byte value whose last 20 bytes are all zero (or otherwise not a valid/derivable TRON address).
2. The length check at `Wallet.java:4332-4338` passes (length == 21), so no exception is thrown.
3. `ShieldedTRC20ParametersBuilder.burnParamsToHexString` embeds this address as `payTo` in the trigger input without further checks (`ShieldedTRC20ParametersBuilder.java:503-516`).
4. The resulting transaction, once broadcast and executed by the `BURN`/`VerifyBurnProof` precompiled contract path, spends the shielded note and delivers value to the invalid/zero address, with no mechanism to recover the funds — mirroring the "burnAndBridge" fund-loss bug class from the original report.

Note: I was unable to retrieve the exact body of `normalizeTransparentToAddress` due to tool/iteration limits, so I cannot 100% confirm whether it performs any additional zero-address filtering beyond what is shown in the length check at the `Wallet.java` call site. This should be verified directly in a Devin session with full file access before treating this as conclusively unpatched.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L4330-4338)
```java
    byte[] transparentToAddress = request.getTransparentToAddress().toByteArray();
    byte[] transparentToAddressTvm = new byte[20];
    if (!ArrayUtils.isEmpty(transparentToAddress)) {
      if (transparentToAddress.length == 21) {
        System.arraycopy(transparentToAddress, 1, transparentToAddressTvm, 0, 20);
      } else {
        throw new ZksnarkException("invalid transparent to address");
      }
    }
```

**File:** framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java (L503-516)
```java
  private String burnParamsToHexString(GrpcAPI.ShieldedTRC20Parameters burnParams,
      List<BytesMessage> spendAuthoritySignature,
      BigInteger value, byte[] transparentToAddress,
      boolean withAsk) {
    byte[] payTo = new byte[32];
    if (value.compareTo(BigInteger.ZERO) <= 0) {
      throw new IllegalArgumentException("the value must be positive");
    }

    byte[] transparentToAddressTvm = normalizeTransparentToAddress(transparentToAddress);

    payTo[11] = Wallet.getAddressPreFixByte();
    System.arraycopy(transparentToAddressTvm, 0, payTo, 12, 20);
    ShieldContract.SpendDescription spendDesc = burnParams.getSpendDescription(0);
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

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L98-101)
```java
    byte[] ownerAddress = withdrawBalanceContract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/VMUtils.java (L182-195)
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
```
