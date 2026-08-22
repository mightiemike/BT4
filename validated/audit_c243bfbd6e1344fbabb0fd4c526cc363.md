Based on my investigation, I've identified the strongest reachable analog in java-tron.

### Title
Cross-Contract Signature Replay in `BatchValidateSign`/`ValidateMultiSign` Precompiles Due to Unbound `messageHash` - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The TVM precompiled contracts `BatchValidateSign` (address `0x9`) and `ValidateMultiSign` (address `0xa`) let any deployed smart contract verify off-chain ECDSA signatures against arbitrary hashes supplied by the caller. Like the `ApprovedCallsPolicy` bug, the signed `hash` is not cryptographically bound to the verifying contract's own address, so a signature produced to authorize an action in one consumer contract can be replayed to satisfy the same check in a different, unrelated contract that happens to derive the same hash for a semantically different action.

### Finding Description
`BatchValidateSign.doExecute` takes a caller-supplied 32-byte `hash` directly from calldata with no binding to `msg.sender`/the contract address performing the check, then recovers signer addresses over that exact hash via `recoverAddrBySign`. [1](#0-0) 
Likewise, `ValidateMultiSign.execute` builds its verification hash only from `(address, permissionId, data)` — the account whose permission is being checked and an app-supplied data blob — but never from the identity of the calling/consuming smart contract that invokes the precompile: [2](#0-1) 
Both precompiles delegate address recovery to the shared helper `recoverAddrBySign`, which only checks `signature.validateComponents()` (range checks on r/s, not canonical low-s enforcement) before recovering the address: [3](#0-2) 
and `ECDSASignature.validateComponents` only bounds `r`/`s` to `[1, SECP256K1N)` without rejecting the malleable high-`s` form: [4](#0-3) 

Because neither precompile incorporates a verifying-contract identifier, chain-specific domain separator, or nonce into the hash it verifies, any Solidity/TVM contract author who builds an off-chain-approval scheme (analogous to `ApprovedCallsPolicy`) on top of these precompiles — using a hash derived only from application data — is exposed to the same class of cross-contract signature replay described in the report: a signer's approval intended for contract A's action can be reused to satisfy contract B's check if both derive an identical hash, since the precompile provides no EIP-712-style domain binding by design.

### Impact Explanation
This is a building-block-level design gap, not a logic bug in the underlying account/permission model itself — `TransactionCapsule.checkWeight`, which is the canonical multisig path used for real on-chain transactions, hashes the full `rawData` (owner, contract type, ref-block, expiration, timestamp), which is unique per transaction and not subject to this issue. However, any deployed smart contract that relies on `ValidateMultiSign`/`BatchValidateSign` to gate privileged state transitions using an application-chosen hash (without independently mixing in its own contract address/chain id/nonce) inherits the replay weakness, potentially enabling unauthorized execution of privileged operations in one contract using a signature obtained for another.

### Likelihood Explanation
Likelihood depends entirely on downstream contract authors' hash construction; the precompiles themselves are reachable by any address via an ordinary contract call, so exploitation requires only that two different consumer contracts (or two different actions within one contract) end up computing the same hash for different intents — a realistic risk for template-based dApp deployments that reuse hashing schemes.

### Recommendation
Document and/or enforce a canonical domain-separated hash format for `ValidateMultiSign`/`BatchValidateSign` usage, requiring the calling contract to mix its own address, chain ID, and a nonce into the hash (EIP-712-style), and update `ECDSASignature.validateComponents`/`recoverAddrBySign` to reject non-canonical (high-`s`) signatures to eliminate malleability regardless of caller-side dedup logic.

### Proof of Concept
1. Deploy `ContractA` and `ContractB`, both computing `hash = sha256(bytes)` from identical application data (e.g., a shared template that hashes only `(actionId, amount)`).
2. Have a `SIGNER`-role holder sign `hash` off-chain and submit it to `ContractA` via `batchvalidatesign(hash, sig, signerAddr)`/`validatemultisign(...)`, authorizing an action in `ContractA`.
3. Submit the same `hash`/`sig` pair to `ContractB`, which independently arrived at the same `hash` for a different action; `BatchValidateSign`/`ValidateMultiSign` returns success because the precompile never checks that the hash was scoped to `ContractB`, allowing replay of the signer's approval across the two unrelated contracts.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L371-388)
```java
  private static byte[] recoverAddrBySign(byte[] sign, byte[] hash) {
    byte[] out = null;
    if (ArrayUtils.isEmpty(sign) || sign.length < 65) {
      return new byte[0];
    }
    try {
      Rsv rsv = Rsv.fromSignature(sign);
      SignatureInterface signature = SignUtils.fromComponents(rsv.getR(), rsv.getS(), rsv.getV(),
          CommonParameter.getInstance().isECKeyCryptoEngine());
      if (signature.validateComponents()) {
        out = SignUtils.signatureToAddress(hash, signature,
            CommonParameter.getInstance().isECKeyCryptoEngine());
      }
    } catch (Throwable any) {
      logger.info("ECRecover error", any.getMessage());
    }
    return out;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1057-1064)
```java
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1163)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-941)
```java
    public static boolean validateComponents(BigInteger r, BigInteger s,
        byte v) {

      if (v != 27 && v != 28) {
        return false;
      }

      if (BIUtil.isLessThan(r, BigInteger.ONE)) {
        return false;
      }
      if (BIUtil.isLessThan(s, BigInteger.ONE)) {
        return false;
      }

      if (!BIUtil.isLessThan(r, SECP256K1N)) {
        return false;
      }
      return BIUtil.isLessThan(s, SECP256K1N);
    }
```
