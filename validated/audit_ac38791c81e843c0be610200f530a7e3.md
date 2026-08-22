### Title
Pre-fork signature-dedup keyed by raw base64 signature (not recovered address) allows single-key weight double-counting - ([File: chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java])

### Summary
`TransactionCapsule.checkWeight` deduplicates submitted signatures by `HashMap addMap` keyed on `base64` (the raw signature bytes) unless `ForkController.instance().pass(VERSION_4_7_1)` is true, in which case the key is switched to `encode58Check(address)` (the recovered signer address). Before the fork activates, an attacker who controls one low-weight key on a multisig `Permission` can produce two syntactically distinct 65-byte ECDSA signatures over the same `hash` that both recover via `SignUtils.signatureToAddress` to the same key address (classic ECDSA signature malleability: `(r, s, v)` and `(r, n-s, 1-v)` recover to the same address), causing `currentWeight` to be summed twice for that single key.

### Finding Description
`checkWeight` (chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java:233-269) loops over `sigs`, computes `base64 = getBase64FromByteString(sig)`, recovers `address = SignUtils.signatureToAddress(hash, base64, ...)`, and only rekeys the dedup map to the recovered `address` (`encode58Check(address)`) when the VERSION_4_7_1 fork has passed: [1](#0-0) 

Prior to the fork, `addMap.containsKey(base64)` checks only the raw signature bytes for duplication, not the recovered signer identity. Because ECDSA over secp256k1 (and the SM2 variant used when the node's crypto engine differs) is malleable — a signature `(r, s)` and its malleable counterpart `(r, n-s)` with the recovery id flipped both validate and recover to the identical address — an attacker holding a single private key can produce two distinct 65-byte signature blobs for the same message hash that both pass `SignUtils.signatureToAddress` and resolve to the same `address`/`weight`. The only length check is `sig.size() < 65` (chainbase/.../TransactionCapsule.java:244), which does not enforce canonical/low-S form, and the pre-fork branch never normalizes on the recovered address, so the second malleable signature is treated as coming from a "different" signer and is not rejected by `addMap.containsKey(base64) → throw "has signed twice!"`. As a result `currentWeight += weight` executes twice for one key, inflating the reported weight.

This is reachable from `GetTransactionApprovedListServlet` (framework/src/main/java/org/tron/core/services/http/GetTransactionApprovedListServlet.java) and the analogous `getTransactionSignWeight` path in `Wallet.java`, both of which call into `TransactionCapsule.checkWeight`/`Manager` to compute `currentWeight` against `permission.getThreshold()`, feeding an unauthenticated RPC endpoint that off-chain custody automation may rely on to gate approvals.

### Impact Explanation
Scoped impact: an unprivileged attacker controlling one low-weight key on a multisig account can cause `getTransactionApprovedList` / `getTransactionSignWeight` to falsely report `TransactionSignWeight.Result.code = ENOUGH_PERMISSION` when the transaction is not actually authorized by sufficient distinct keys, because one key's weight is double-counted. This does not corrupt on-chain consensus state directly (the double-signature could still be rejected by the actual consensus-path `checkWeight` call if that call is also pre-fork and hits the same bug, which is the more severe concern — it could let an actuator/transaction validation logic treat a transaction as sufficiently signed with fewer distinct real approvers, enabling execution of an under-signed multisig transaction). This matches the "unauthorized account operations / asset accounting corruption via multisig weight bypass" bounty class, contingent on the chain being at a pre-VERSION_4_7_1 fork state.

### Likelihood Explanation
The precondition is that the chain/node has not yet activated `VERSION_4_7_1` in `ForkController`; on a fully-synced mainnet/testnet with the fork long since activated, `ForkController.instance().pass(VERSION_4_7_1)` is `true` and the address-keyed dedup path is used, closing this bug. The exploit itself is cheap: the attacker only needs one private key and the ability to produce a malleable variant of an ECDSA signature over the same hash (standard elliptic-curve arithmetic, no special access), and can call the public HTTP servlet repeatedly at no on-chain cost since `getTransactionApprovedList` is a read-only/simulated check, not a state-changing broadcast. The comment in `SignUtils.java` explicitly documents that the loose `size < 65` check in `checkWeight` is intentional for "historical on-chain signatures," corroborating that this pre-fork behavior is a known legacy weakness, mitigated only by fork-height gating rather than being fully fixed for all historical/replay scenarios.

### Recommendation
Always key `addMap` by the recovered signer address (`encode58Check(address)`) regardless of fork state, removing the raw-`base64`-keyed branch entirely, or backport the address-based dedup unconditionally since it is strictly safer and behaviorally equivalent for honest signatures. Additionally, enforce canonical (low-S) signature form at the point of `signatureToAddress`/signature parsing to eliminate ECDSA malleability as an attack vector independent of the fork switch.

### Proof of Concept
```java
// JUnit-style PoC (conceptual, requires an ECKey and a permission with threshold requiring 2 signers)
byte[] hash = Sha256Hash.hash(true, "test".getBytes());
ECKey key = new ECKey();
ECKey.ECDSASignature sig1 = key.sign(hash); // (r, s, v)
// Construct malleable counterpart: same r, s' = N - s, flipped recovery id
BigInteger sPrime = ECKey.CURVE.getN().subtract(sig1.s);
ECKey.ECDSASignature sig2 = new ECKey.ECDSASignature(sig1.r, sPrime);
sig2.v = (byte) (sig1.v == 27 ? 28 : 27); // flip recovery id, still recovers same address

List<ByteString> sigs = Arrays.asList(
    ByteString.copyFrom(sig1.toByteArray()),
    ByteString.copyFrom(sig2.toByteArray()));

Permission permission = /* threshold=2, single key = key.getAddress(), weight=1 */;

// Pre-fork: ForkController not passed VERSION_4_7_1
long weightPreFork = TransactionCapsule.checkWeight(permission, sigs, hash, null);
assertEquals(2, weightPreFork); // BUG: single key's weight (1) counted twice

// Post-fork: ForkController.instance().pass(VERSION_4_7_1) == true
// Expect PermissionException("... has signed twice!") to be thrown instead.
```

Expected/actual: pre-fork, `checkWeight` returns `currentWeight = 2` for a single 1-weight key, incorrectly satisfying a `threshold = 2` requirement and causing `getTransactionApprovedList`/`getTransactionSignWeight` to report `ENOUGH_PERMISSION` for an under-signed transaction; post-fork the second malleable signature is correctly rejected with `PermissionException`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L248-267)
```java
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
```
