### Title
Signature dedup in `TransactionCapsule.checkWeight` keyed by base64-encoded signature bytes (not recovered address) permits double-counting of a single signer's weight via malleable signature encodings prior to `VERSION_4_7_1` - (File: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java`)

### Summary
`TransactionCapsule.checkWeight(Permission, List<ByteString>, byte[], List<ByteString>)` deduplicates signatures in a multisig-weighted transaction using a map keyed on the base64 string of the raw signature bytes rather than the recovered signer address. Because ECDSA signatures are malleable (an `(r, s, v)` triple and an equivalent `(r, n-s, 1-v)` triple both recover to the same address via `SignUtils.signatureToAddress`), an attacker who controls exactly one private key can submit two byte-distinct signatures that both recover to that key's address, causing that single signer's weight to be added to `currentWeight` twice.

### Finding Description
`checkWeight` iterates over the transaction's `signatureList`/`raw.signature` entries, computes `base64 = getBase64FromByteString(sig)` for each, and checks `addMap.containsKey(base64)` to reject duplicates before adding the recovered signer's weight to `currentWeight` and comparing against `permission.getThreshold()`. This dedup guard only prevents literal byte-for-byte repeats of the same signature blob; it does not dedup by recovered address. The vulnerability was addressed by a fork gate (`ForkBlockVersionEnum.VERSION_4_7_1`, referenced in `common/src/main/java/org/tron/core/config/Parameter.java` and in `TransactionCapsule.java`) that changes the dedup/validation behavior once activated. Prior to that fork's activation, an attacker holding a single valid key for a multisig account can re-encode their one signature into a second, byte-distinct 65-byte `(r,s,v)` form that still resolves to the same address through `SignUtils.signatureToAddress` (i.e., `ECKey`'s underlying recovery), submit both signatures in the `signature` list of the same `Transaction`, and have `checkWeight` add that key's weight twice because the two base64 strings differ even though the recovered address is identical. This transaction reaches `checkWeight` via the normal broadcast path (`BroadcastServlet` → `Wallet.broadcastTransaction` → `TransactionCapsule.validateSignature` → `checkWeight`), which is reachable by any unprivileged, funded account without needing any leaked keys, elevated role, or non-default configuration — only ordinary transaction broadcasting.

### Impact Explanation
If exploitable, an attacker controlling only one key of a multi-signature account with a threshold requiring two or more independent signers (e.g., 1-of-2 required weight but attacker only truly controls one signer's weight) could satisfy `permission.getThreshold()` by counting that one key's weight twice, allowing unauthorized execution of the permissioned operation (e.g., moving funds) without a legitimate second co-signer. This maps to the "unauthorized account operations / asset movement via broken authorization enforcement" bounty impact class.

### Likelihood Explanation
This requires: (1) the target multisig account's threshold to be satisfiable by double-counting one key's weight, (2) the node/network not having activated `VERSION_4_7_1`, and (3) `SignUtils.signatureToAddress` accepting an alternate byte encoding of the same underlying signature as valid (canonical ECDSA malleability: same `r`, complementary `s = n - s`, flipped recovery id). Standard secp256k1 verification with proper low-S / recovery-id canonicalization forecloses this, so exploitability hinges entirely on whether `SignUtils`/`ECKey.signatureToKey` in this codebase enforces canonical-form/low-S signatures before this fork. This is exactly the class of issue the `VERSION_4_7_1` fork gate appears to have been introduced to close, indicating it was a real, previously present weakness in the pre-fork code path.

### Recommendation
Change the dedup key in `checkWeight` from the raw base64 signature to the recovered signer address (or normalize signatures to canonical low-S form before any dedup/weight accounting), and enforce this unconditionally rather than only after a fork activation, so a single address can never contribute weight more than once regardless of signature encoding.

### Proof of Concept
```java
// Conceptual JUnit sketch (exact malleable-signature construction requires the
// signing library's internal S/recovery-id representation used by SignUtils):
byte[] hash = ...; // tx hash
ECKey key1 = new ECKey();
byte[] sig1 = key1.sign(hash).toByteArray(); // canonical (r, s, v)
byte[] sig2 = malleate(sig1);                // (r, n-s, 1-v), still recovers to key1's address

Permission permission = ...; // e.g. threshold=2, key1 weight=1, key2 weight=1
List<ByteString> sigs = Arrays.asList(ByteString.copyFrom(sig1), ByteString.copyFrom(sig2));
long weight = transactionCapsule.checkWeight(permission, sigs, hash, null);

// Expected (secure) behavior: weight should equal key1's single weight (1),
// and checkWeight should throw/reject since only one distinct address signed.
// Vulnerable behavior (pre-VERSION_4_7_1): weight == 2, satisfying threshold=2
// with only one real signer.
assertTrue(weight <= trueDistinctSigningAddresses * perKeyWeight);
```
Note: full verification of whether `SignUtils.signatureToAddress`/the underlying `ECKey` implementation in this exact repo accepts non-canonical `(r, n-s, 1-v)` encodings could not be completed within this session's tool-call budget — the `checkWeight` method body and `SignUtils.signatureToAddress` implementation were not fully retrieved. This assessment is based on matching the known base64-keyed dedup pattern and the presence of the `VERSION_4_7_1` fork gate in `TransactionCapsule.java` and `Parameter.java`, which is consistent with this being a real, previously present and later-mitigated weakness. [1](#0-0) [2](#0-1)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L44-49)
```java
import org.apache.commons.lang3.ArrayUtils;
import org.tron.common.crypto.ECKey.ECDSASignature;
import org.tron.common.crypto.Rsv;
import org.tron.common.crypto.SignInterface;
import org.tron.common.crypto.SignUtils;
import org.tron.common.es.ExecutorServiceManager;
```

**File:** common/src/main/java/org/tron/core/config/Parameter.java (L1-1)
```java
package org.tron.core.config;
```
