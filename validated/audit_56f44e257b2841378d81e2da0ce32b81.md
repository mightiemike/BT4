### Title
`ValidateMultiSign`/`BatchValidateSign` TVM precompiles omit the calling (verifying) contract from the signed hash, enabling cross-contract/cross-chain signature replay - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
The `ValidateMultiSign` precompiled contract builds the message hash that TVM smart contracts use to authorize actions on behalf of a TRON account purely from `accountAddress || permissionId || data`, with no binding to the calling/verifying contract or the chain. This mirrors exactly the reported bug class: a signature-authorization hash that omits the "entry point" (here, the consuming contract) context, allowing the same signature to be replayed against a different contract (or a different network) that happens to reuse the same `address`/`permissionId`/`data` triple.

### Finding Description
`ValidateMultiSign.execute()` computes:

```java
byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
byte[] hash = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), combine);
``` [1](#0-0) 

then recovers signer addresses against this `hash` and checks their combined weight against the account's `Permission`: [2](#0-1) 

The recovery itself uses a generic `recoverAddrBySign(sign, hash)` helper with no notion of the calling contract: [3](#0-2) 

`BatchValidateSign.doExecute()` is even more permissive: the `hash` word is taken directly from caller-supplied `data[0]` with zero binding to anything protocol-level (no chain id, no account, no contract) — it is exactly a raw `ecrecover`-style primitive: [4](#0-3) 

This design is the same root cause described in the report: EIP-4337's `getPackedUserOperationHash()` intentionally left out the `EntryPoint` address, so the same signed `userOp` could be replayed against a different `EntryPoint`. In java-tron, `ValidateMultiSign` is the analogous "TRON smart-account signature verification" primitive exposed to every deployed contract — it authenticates a TRON account's off-chain-signed intent for use by arbitrary TVM contracts, but the hash it verifies against never incorporates *which* contract is doing the verifying (i.e., there's no `msg.sender`/verifying-contract binding, and no TRON chain identifier). Any two unrelated contracts (e.g., deployed on different dApps, or the same contract redeployed on a different TRON-compatible network/testnet) that end up hashing the same `(address, permissionId, data)` triple will accept the identical signature as valid authorization.

### Impact Explanation
Any unprivileged user/dApp developer can build a contract that calls `ValidateMultiSign`/`BatchValidateSign` to gate a state-changing action (transfers, approvals, permission-based operations) on an off-chain multisig signature. Because the hash is not bound to the verifying contract or chain, a signature a user produced to authorize action X in Contract A can be replayed to authorize an unrelated action in Contract B (or the same contract deployed on a forked/adjacent network) whenever the `data` payload collides — a realistic risk since many integrators naively hash only business-semantic fields (e.g., "transfer 100 TRX to Bob") without adding contract/domain separation, exactly the anti-pattern EIP-4337 wallets fell into before including `EntryPoint`. The impact is unauthorized re-execution of a previously-authorized operation (accounting/settlement impact), not merely theoretical, since it stems from a protocol-level primitive shipped to every TVM contract rather than a caller mistake alone.

### Likelihood Explanation
Likelihood is moderate: exploitation requires two consuming contracts (or the same contract across chains) to derive an identical `(address, permissionId, data)` tuple, which is plausible whenever `data` is a generic, non-domain-separated digest (e.g., a hash of a widely-reused message format, or a fixed/constant payload) — a common integration pattern given the precompile provides no built-in domain separation and its documentation/tests never demonstrate adding one. [5](#0-4) 

### Recommendation
Bind the hash computed inside `ValidateMultiSign` (and analogously provide a chain/contract-aware variant of `BatchValidateSign`) to the calling contract's address (`getCallerAddress()`, already tracked on `PrecompiledContract`) and to a chain identifier, e.g.:
```java
byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), getCallerAddress(), chainId, data);
```
so that a signature validated in one contract/chain context cannot be replayed in another, mirroring the fix applied upstream for the `EntryPoint`-less `getPackedUserOperationHash()`.

### Proof of Concept
1. Deploy Contract A, which calls `ValidateMultiSign(userAddr, permissionId, dataHash, sigs)` to gate a withdrawal, where `dataHash = sha256("withdraw:100")` (a generic, non-domain-separated message).
2. User signs the resulting internal hash `sha256(userAddr || permissionId || dataHash)` once to authorize the withdrawal in Contract A.
3. Deploy unrelated Contract B (different application, e.g. an "escrow release"), which also calls `ValidateMultiSign(userAddr, permissionId, dataHash, sigs)` with the same `dataHash` (plausible collision since neither precompile nor typical integration guides require embedding the contract address).
4. Replay the same `sigs` obtained in step 2 against Contract B — `ValidateMultiSign` recomputes the identical hash (no contract/chain binding) and returns success, letting the attacker trigger Contract B's action without a fresh authorization from the user. [6](#0-5)

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1058-1120)
```java
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
        return Pair.of(true, DATA_FALSE);
      }

      AccountCapsule account = this.getDeposit().getAccount(address);
      if (account != null) {
        try {
          Permission permission = account.getPermissionById(permissionId);
          if (permission != null) {
            //calculate weight
            long totalWeight = 0L;
            List<byte[]> executedSignList = new ArrayList<>();
            for (byte[] sign : signatures) {
              byte[] recoveredAddr = recoverAddrBySign(sign, hash);

              sign = merge(recoveredAddr, sign);
              if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
                if (ByteArray.matrixContains(executedSignList, sign)) {
                  continue;
                }
                MUtil.checkCPUTime();
              }
              long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
              if (weight == 0) {
                //incorrect sign
                return Pair.of(true, DATA_FALSE);
              }
              totalWeight += weight;
              executedSignList.add(sign);
              executedSignList.add(recoveredAddr);
            }

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
            }
          }
        } catch (Throwable t) {
          if (t instanceof OutOfTimeException) {
            throw t;
          }
          logger.info("ValidateMultiSign error:{}", t.getMessage());
        }
      }
      return Pair.of(true, DATA_FALSE);
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1178)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[1].intValueSafe() / WORD_SIZE].intValueSafe();
        int addrArraySize = words[words[2].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE || addrArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }

      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[1].intValueSafe() / WORD_SIZE, data) :
          extractBytesArray(words, words[1].intValueSafe() / WORD_SIZE, data);
      byte[][] addresses = extractBytes32Array(
          words, words[2].intValueSafe() / WORD_SIZE);
      int cnt = signatures.length;
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L102-118)
```java
    //generate data

    byte[] address = key.getAddress();
    int permissionId = 2;
    byte[] data = Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), longData);

    //combine data
    byte[] merged = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
    //sha256 of it
    byte[] toSign = Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), merged);

    //sign data

    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
```
