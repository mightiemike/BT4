### Title
`ValidateMultiSign` precompile has no nonce/expiry in the signed hash, allowing off-chain multi-sig authorizations to be replayed or delayed indefinitely - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` TVM precompiled contract lets a smart contract verify that a threshold of an account's `Permission` keys signed some application-defined `data` blob. The hash that signers actually sign is computed purely from `address || permissionId || data`, with no nonce or expiration bound to it, so any valid quorum of signatures can be reused or held back and submitted at an arbitrary future time — exactly the "signature nonce/expiry" bug class from the external report.

### Finding Description
`ValidateMultiSign.execute` builds the message that off-chain signers sign as:
```
combine = address || permissionId || data
hash = sha256(combine)
``` [1](#0-0) 

It then recovers each signer's address from `hash`, looks up their weight in the account's current `Permission` (fetched live from `getDeposit().getAccount(address)` / `account.getPermissionById(permissionId)`), sums weights, and returns "true" once the threshold is met: [2](#0-1) 

There is no on-chain nonce that increments per use, and no expiry timestamp folded into `hash`. Consequently:
- Nothing in the precompile itself prevents two conflicting sets of signatures for the same `permissionId`/`data` from both being usable — a caller (`msg.sender` of the contract invoking this precompile) fully controls when/if a saved signature set is submitted.
- If a signer initially withholds a signature, later obtains a new key that is added to the account's `Permission` via `AccountPermissionUpdateContract` (validated by `AccountPermissionUpdateActuator.checkPermission`, which does not invalidate previously-signed off-chain payloads), or if weights/threshold change over time, an old signature set collected under a *different* permission configuration can still be replayed as long as the recovered addresses still hold sufficient weight under the *current* permission. `checkWeight`/`getWeight` in `TransactionCapsule` (used analogously for on-chain tx signatures) demonstrate the pattern of validating signatures purely against current permission state without any freshness binding, and `ValidateMultiSign` follows the same pattern without even the transaction-level protections. [3](#0-2) 

By contrast, ordinary java-tron transactions (e.g. `TransferContract`, `AccountPermissionUpdateContract`) are inherently protected because the signed hash is `Sha256(Transaction.raw)`, which embeds `ref_block_hash`/`ref_block_bytes` and an `expiration` timestamp, and the executed transaction ID is deduplicated on-chain — so normal transaction signatures cannot be stockpiled and replayed months later. `ValidateMultiSign`, however, is a general-purpose primitive exposed to arbitrary smart contracts via `TriggerSmartContract`, and it deliberately signs only `address || permissionId || data` with no such binding, so any dApp built on it inherits the described nonce/expiry weakness at the protocol level rather than at the application level, since the primitive itself provides no anti-replay guarantee comparable to what native transactions get for free.

### Impact Explanation
Any smart contract that uses `ValidateMultiSign` as its multi-sig gate (e.g., DAOs, custodial wallets, governance modules built on TRON) inherits exactly the scenario from the external report: a reluctant signer's signature, once produced, remains permanently valid for that exact `(address, permissionId, data)` triple. It can be combined with newly obtained signatures (e.g., after a controller's weight/threshold is changed) and submitted at a moment advantageous to whoever holds the signature set, without consent of the originally intended quorum at that later time. This can result in unauthorized state changes in whatever contract logic gates on this precompile (fund transfers, permission changes, parameter changes), i.e., unauthorized account/contract operation and potential asset/accounting corruption — matching the "unauthorized account operation" acceptance criterion.

### Likelihood Explanation
Low-to-moderate: it requires (1) a contract author to build multi-sig approval logic on `ValidateMultiSign` without independently adding a nonce/expiry to `data`, and (2) an adversarial or lapsed signer to retain a valid signature and find a later moment (e.g., after a permission/weight change) where replay is beneficial. This mirrors the report's own assessment ("Low, required mistakes and unique parity of voting power"), since the core protocol primitive is not itself exploited directly but sets a trap for downstream contracts.

### Recommendation
- Document prominently (and ideally enforce at the precompile or ABI level) that `data` passed to `ValidateMultiSign` must itself include an application-managed nonce and expiry that the calling contract checks/increments, since the precompile provides no anti-replay guarantee.
- Consider extending `ValidateMultiSign`'s hash preimage to optionally include a caller-supplied nonce/expiry word that the precompile itself can validate against a per-account counter/timestamp (analogous to `ControllersStorage.layout().nonce` in the original report), removing the burden from individual dApp authors.
- At minimum, add TVM developer documentation/warnings and Solidity library examples showing safe usage with nonce/expiry embedded in `data`.

### Proof of Concept
1. Account `X` has `Permission` (id=2) with keys `A(30), B(30), C(30)`, threshold 60.
2. A dApp calls `ValidateMultiSign(X, 2, data)` where `data` encodes "set fee = 40%". `A` and `B` sign `sha256(X || 2 || data)`; `C` refuses. The dApp caller stores `sigA, sigB`.
3. Later, `X`'s owner submits `AccountPermissionUpdateContract` (validated by `AccountPermissionUpdateActuator.checkPermission`, no cross-check against outstanding off-chain signature sets) changing permission id=2 to keys `A(30), B(30), D(40)` where `D` is controlled by the same party as former `C`.
4. `D` now signs the *same* `data`/hash (since `hash` doesn't depend on the permission's key set, only on `address||permissionId||data`, and `permissionId` 2 is reused). `sigA, sigB, sigD` together reach weight ≥ threshold in `ValidateMultiSign`'s current permission lookup.
5. The one-year-old approval for "set fee = 40%" — never actually agreed to by the current key holders as a set — is accepted, because `execute()` only checks current weights and threshold, not any timestamp or usage counter. [2](#0-1)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1057-1065)
```java
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1111)
```java
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L233-270)
```java
  public static long checkWeight(Permission permission, List<ByteString> sigs, byte[] hash,
      List<ByteString> approveList)
      throws SignatureException, PermissionException, SignatureFormatException {
    long currentWeight = 0;
    if (sigs.size() > permission.getKeysCount()) {
      throw new PermissionException(
          "Signature count is " + (sigs.size()) + " more than key counts of permission : "
              + permission.getKeysCount());
    }
    HashMap addMap = new HashMap();
    for (ByteString sig : sigs) {
      if (sig.size() < 65) {
        throw new SignatureFormatException(
            "Signature size is " + sig.size());
      }
      String base64 = TransactionCapsule.getBase64FromByteString(sig);
      byte[] address = SignUtils
          .signatureToAddress(hash, base64, CommonParameter.getInstance().isECKeyCryptoEngine());
      long weight = getWeight(permission, address);
      if (weight == 0) {
        throw new PermissionException(
            ByteArray.toHexString(hash) + " is signed by " + encode58Check(address)
                + " but it is not contained of permission.");
      }
      if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
        base64 = encode58Check(address);
      }
      if (addMap.containsKey(base64)) {
        throw new PermissionException(encode58Check(address) + " has signed twice!");
      }
      addMap.put(base64, weight);
      if (approveList != null) {
        approveList.add(ByteString.copyFrom(address)); //out put approve list.
      }
      currentWeight += weight;
    }
    return currentWeight;
  }
```
