I found a strong analog here. The `checkPublicAmount` / `getShieldedContractScalingFactor` flow in `Wallet.java` trusts a user-supplied shielded TRC20 contract address and calls `scalingFactor()` on it without ever validating that the address is a deployed contract or that it conforms to the expected `IShieldedTRC20` interface (i.e. it doesn't check the target actually has code, is a legitimate shielded pool contract, or even revert-checks properly beyond `scalingFactor > 0`). This mirrors the reported bug class: an externally-supplied "manager" contract address is trusted and used to derive security-critical accounting values (`scaledFromAmount`/`scaledToAmount`) without validating it implements the expected role/interface.### Title
Unvalidated shielded TRC-20 contract address is trusted for scaling-factor-based accounting - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
`Wallet.createShieldedContractParametersWithoutAsk` (and its sibling `createShieldedContractParameters`) accepts a caller-supplied `shieldedTRC20ContractAddress`, only checks that it is 21 bytes long, and then calls `getShieldedContractScalingFactor()` on that address to compute the scaling factor used to convert public TRX/TRC-20 amounts into the shielded-pool's internal note value units. There is no check that the address is a deployed contract, let alone that it is the legitimate, protocol-approved `IShieldedTRC20` implementation. This is the same root-cause pattern as the reported `ConcreteStorage.updateAccessControl` bug: a critical "manager"-like contract address is trusted to conform to an expected interface/behavior without any validation, and its return value is used directly in security-critical accounting logic.

### Finding Description
In `createShieldedContractParametersWithoutAsk`, the only validation performed on the target address is a length check: [1](#0-0) 

The address is then used to derive the scaling factor that governs how public `fromAmount`/`toAmount` values are converted to the internal accounting units used for mint/transfer/burn of shielded notes: [2](#0-1) 

`checkPublicAmount` calls `getShieldedContractScalingFactor(address)` and only validates that the returned value is a positive integer and that `fromAmount`/`toAmount` are exact multiples of it — it never verifies that `address` is actually a contract, that it has code, or that it implements the expected `scalingFactor()` semantics of the real shielded pool contract: [3](#0-2) 

`getShieldedContractScalingFactor` blindly triggers a constant call against the caller-supplied address and treats whatever bytes come back (including from an EOA/non-existent address, which the TVM would return as empty data, or from a completely unrelated/malicious contract) as the scaling factor: [4](#0-3) 

This mirrors the external report's core issue: a contract address that is expected to conform to a specific interface (`AccessControlManager` in the report; the canonical `ShieldedTRC20` contract here) is accepted and relied upon for security-relevant computations without any interface/existence validation.

### Impact Explanation
The scaling factor directly controls how the transparent `fromAmount`/`toAmount` (public TRX/TRC-10/TRC-20 values) are converted into the internal shielded-note value units (`scaledFromAmount`/`scaledToAmount`), which then determine how many shielded notes are minted or how much is burned. If a caller points this API at an arbitrary, attacker-controlled address (rather than the legitimate deployed shielded pool contract), the "scaling factor" returned is entirely attacker-chosen. Depending on how the resulting `ShieldedTRC20Parameters` are subsequently submitted and processed by the actual on-chain shielded pool contract (which independently reads its own configured `scalingFactor()`), this can create a mismatch between the client-computed scaled amounts embedded in zk-proof-adjacent parameters and the amounts the real contract expects — leading to either failed transactions (denial of service against legitimate users) or, in the worst case, incorrect accounting parameters being accepted by validators/relayers who trust wallet-computed values without independently re-deriving them from the canonical contract. At minimum this represents an accounting/validation gap that undermines the integrity guarantee that the "manager" (shielded pool) parameters used for value conversion are authentic.

### Likelihood Explanation
This is reachable by any unprivileged user/API caller of `createShieldedContractParametersWithoutAsk`/`createShieldedContractParameters` (exposed via gRPC/HTTP wallet APIs) by simply supplying an arbitrary 21-byte address as `shieldedTRC20ContractAddress` — no special privileges are required, and the only gate is `checkAllowShieldedTransactionApi()`, which is a global feature flag, not an authorization check on the specific contract address.

### Recommendation
Validate that `shieldedTRC20ContractAddress` corresponds to a known/whitelisted, deployed shielded TRC-20 pool contract before trusting its `scalingFactor()` response — e.g., check the address exists in the contract store, has bytecode, and/or matches a governance-approved allowlist of shielded pool contract addresses, analogous to requiring `AccessControlManager(...).hasRole(...)` in the original report's remediation.

### Proof of Concept
1. Call the wallet API `createShieldedContractParametersWithoutAsk` (or `createShieldedContractParameters`) with a `shieldedTRC20ContractAddress` pointing to an attacker-deployed contract that implements a `scalingFactor()` function returning an arbitrary attacker-chosen positive integer.
2. `checkPublicAmount` at [3](#0-2)  accepts this value uncritically as long as it is positive and evenly divides `fromAmount`/`toAmount`.
3. The resulting `scaledFromAmount`/`scaledToAmount` are computed using the attacker-controlled scaling factor rather than the real shielded pool's configured value, producing parameters inconsistent with the legitimate contract's accounting and enabling failed/incorrect shielded transfer construction.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3753-3760)
```java
    byte[] shieldedTRC20ContractAddress = request.getShieldedTRC20ContractAddress().toByteArray();
    if (ArrayUtils.isEmpty(shieldedTRC20ContractAddress)
        || shieldedTRC20ContractAddress.length != 21) {
      throw new ContractValidateException("No valid shielded TRC-20 contract address");
    }
    byte[] shieldedTRC20ContractAddressTvm = new byte[20];
    System.arraycopy(shieldedTRC20ContractAddress, 1, shieldedTRC20ContractAddressTvm, 0, 20);
    builder.setShieldedTRC20Address(shieldedTRC20ContractAddressTvm);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3770-3773)
```java
    long[] scaledPublicAmount = checkPublicAmount(shieldedTRC20ContractAddress,
        fromAmount, toAmount);
    long scaledFromAmount = scaledPublicAmount[0];
    long scaledToAmount = scaledPublicAmount[1];
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L4223-4254)
```java
  private long[] checkPublicAmount(byte[] address, BigInteger fromAmount, BigInteger toAmount)
      throws ContractExeException, ContractValidateException {
    checkBigIntegerRange(fromAmount);
    checkBigIntegerRange(toAmount);

    BigInteger scalingFactor;
    try {
      byte[] scalingFactorBytes = getShieldedContractScalingFactor(address);
      scalingFactor = ByteUtil.bytesToBigInteger(scalingFactorBytes);
    } catch (ContractExeException e) {
      throw new ContractExeException("Get shielded contract scalingFactor failed");
    }
    if (scalingFactor.compareTo(BigInteger.ZERO) <= 0) {
      throw new ContractValidateException("scalingFactor must be positive");
    }

    // fromAmount and toAmount must be a multiple of scalingFactor
    if (!(fromAmount.mod(scalingFactor).equals(BigInteger.ZERO)
        && toAmount.mod(scalingFactor).equals(BigInteger.ZERO))) {
      throw new ContractValidateException("fromAmount or toAmount invalid");
    }

    long[] ret = new long[2];
    try {
      ret[0] = fromAmount.divide(scalingFactor).longValueExact();
      ret[1] = toAmount.divide(scalingFactor).longValueExact();
    } catch (ArithmeticException e) {
      throw new ContractValidateException("fromAmount or toAmount invalid");
    }

    return ret;
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L4265-4319)
```java
  public byte[] getShieldedContractScalingFactor(byte[] contractAddress)
      throws ContractExeException {
    String methodSign = "scalingFactor()";
    byte[] selector = new byte[4];
    System.arraycopy(Hash.sha3(methodSign.getBytes()), 0, selector, 0, 4);

    TriggerSmartContract.Builder triggerBuilder = TriggerSmartContract.newBuilder();
    triggerBuilder.setContractAddress(ByteString.copyFrom(contractAddress));
    triggerBuilder.setData(ByteString.copyFrom(selector));
    TriggerSmartContract trigger = triggerBuilder.build();

    TransactionExtention.Builder trxExtBuilder = TransactionExtention.newBuilder();
    Return.Builder retBuilder = Return.newBuilder();
    TransactionExtention trxExt;

    try {
      TransactionCapsule trxCap = createTransactionCapsule(trigger,
          ContractType.TriggerSmartContract);
      Transaction trx = triggerConstantContract(trigger, trxCap, trxExtBuilder, retBuilder);

      retBuilder.setResult(true).setCode(response_code.SUCCESS);
      trxExtBuilder.setTransaction(trx);
      trxExtBuilder.setTxid(trxCap.getTransactionId().getByteString());
      trxExtBuilder.setResult(retBuilder);
    } catch (ContractValidateException | VMIllegalException e) {
      retBuilder.setResult(false).setCode(response_code.CONTRACT_VALIDATE_ERROR)
          .setMessage(ByteString.copyFromUtf8(CONTRACT_VALIDATE_ERROR + e.getMessage()));
      trxExtBuilder.setResult(retBuilder);
      logger.warn(CONTRACT_VALIDATE_EXCEPTION, e.getMessage());
    } catch (RuntimeException e) {
      retBuilder.setResult(false).setCode(response_code.CONTRACT_EXE_ERROR)
          .setMessage(ByteString.copyFromUtf8(e.getClass() + " : " + e.getMessage()));
      trxExtBuilder.setResult(retBuilder);
      logger.warn("When run constant call in VM, failed for reason: " + e.getMessage());
    } catch (Exception e) {
      retBuilder.setResult(false).setCode(response_code.OTHER_ERROR)
          .setMessage(ByteString.copyFromUtf8(e.getClass() + " : " + e.getMessage()));
      trxExtBuilder.setResult(retBuilder);
      logger.warn("Unknown exception caught: " + e.getMessage(), e);
    } finally {
      trxExt = trxExtBuilder.build();
    }

    String code = trxExt.getResult().getCode().toString();
    if ("SUCCESS".equals(code)) {
      List<ByteString> list = trxExt.getConstantResultList();
      byte[] listBytes = new byte[0];
      for (ByteString bs : list) {
        listBytes = ByteUtil.merge(listBytes, bs.toByteArray());
      }
      return listBytes;
    } else {
      throw new ContractExeException("trigger contract to get scaling factor error.");
    }
  }
```
