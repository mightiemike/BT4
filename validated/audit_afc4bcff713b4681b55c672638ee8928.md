### Title
Weight double-counting via malleable/duplicate signatures in `ValidateMultiSign` precompile bypasses multisig threshold - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract aggregates signer weights to determine whether a permission's threshold is met, but its de-duplication logic only skips a signature if the exact `(recoveredAddr, sig)` byte pair was already processed. If the same address recovers from two *different* signature byte-encodings (e.g. malleable ECDSA signatures over the same hash), the loop does not skip weight accrual — it only calls `MUtil.checkCPUTime()` and then continues to add that signer's weight a second time. This is structurally analogous to the reported Panoptic issue: a check meant to enforce "one distinct item counts once" can be defeated by supplying multiple representations of the same logical item, allowing a threshold/solvency-style check to be satisfied fraudulently.

### Finding Description
In `PrecompiledContracts.ValidateMultiSign.execute()`: [1](#0-0) 

```java
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
    return Pair.of(true, DATA_FALSE);
  }
  totalWeight += weight;
  executedSignList.add(sign);
  executedSignList.add(recoveredAddr);
}
if (totalWeight >= permission.getThreshold()) {
  return Pair.of(true, dataOne());
}
```

The dedup check `ByteArray.matrixContains(executedSignList, recoveredAddr)` correctly detects that the *address* has already contributed weight, but the inner check only `continue`s (skips adding weight again) when the *exact byte-for-byte signature* is a repeat. When the address is the same but the raw signature bytes differ (any second valid signature over the same `hash` recoverable to the same address — e.g. an ECDSA signature and its malleable counterpart, or simply a second independently-generated signature by the same private key with different `s`/`v` — see the `s`/`v` fields consumed by `SignUtils`/`ECKey`, referenced via `recoverAddrBySign`), execution falls through past the `continue` and re-adds `weight` for the same signer to `totalWeight`.

This directly parallels the audited bug class: `_validatePositionList()` failed to reject duplicate logical entries because its fingerprint check treated different representations (or repeated occurrences) of the same item as distinct/valid, letting `totalWeight`/collateral appear larger than the true, deduplicated value. Here, one private key holder can submit `sign` and a variant `sign'` that both recover to the same address, and the loop counts that single signer's weight twice (or more, up to `MAX_SIZE`), inflating `totalWeight` used in the threshold comparison.

By contrast, `TransactionCapsule.checkWeight()` (used for on-chain transaction signature validation) correctly guards against this via `addMap.containsKey(base64)` (or address-based key after fork `VERSION_4_7_1`) which rejects any second occurrence of the *same address*, not just the same signature bytes — confirming that address-level (not signature-byte-level) dedup is the intended/correct semantic, and that `ValidateMultiSign` deviates from it. [2](#0-1) 

### Impact Explanation
`ValidateMultiSign` is a public TVM precompiled contract (address-exposed opcode) callable by any smart contract or externally-triggered transaction, and is commonly used by dApps/smart contracts to implement on-chain multisig authorization gates (e.g., custody, escrow, governance-style approvals) without relying on the account-permission system. If a single signer can make their own weight count multiple times toward `permission.getThreshold()`, a caller holding only one authorized key (with a sub-threshold weight) can forge apparent multi-party approval and pass the multisig check performed inside a smart contract, bypassing the authorization/accounting invariant the contract relies on. This is an authorization/accounting bypass with concrete on-chain impact (unauthorized approval of privileged smart-contract actions gated by this precompile), matching the "invalid-state/authorization bypass" impact class analogous to the referenced solvency-check bypass.

### Likelihood Explanation
Exploitability requires only that the attacker control one private key that is a member of the target `Permission`, and be able to produce at least two distinct valid signature encodings over the same `hash` that recover to that same address (well known via ECDSA signature malleability / re-signing) — both trivially achievable by the key holder off-chain, with no special privilege needed. The `ValidateMultiSign` precompile is a standard, unprivileged, publicly reachable TVM primitive, so any contract depending on it for multisig gating is exposed.

### Recommendation
Change the dedup check to reject (skip weight accrual for) any signature whose recovered address has already appeared in `executedSignList`, regardless of whether the exact signature bytes match — i.e., mirror `TransactionCapsule.checkWeight()`'s address-based dedup (reject/no-op on repeated address rather than only on repeated exact signature bytes). Concretely, remove the "same address but different signature bytes" fallthrough path and simply `continue` (or return failure) whenever `recoveredAddr` is already present in `executedSignList`.

### Proof of Concept
1. Deploy/target an account with an `Active` permission containing two keys, `key1` (weight 1) and `key2` (weight 1), threshold `2`.
2. As the holder of only `key1`, sign the same `hash` twice, producing two distinct signature byte strings `sig1` and `sig1'` that both recover to `key1`'s address (e.g., via malleable `(r, s)` → `(r, n-s)` with adjusted recovery id, or any two independent ECDSA signings of the same digest which naturally differ in `s`/nonce yet recover identically).
3. Call `ValidateMultiSign(address, permissionId, data, [sig1, sig1'])`.
4. In the loop: iteration 1 adds weight 1 for `key1` (`totalWeight=1`); iteration 2 detects `recoveredAddr` already in `executedSignList` but `sign` (full bytes) is not an exact match, so it does not `continue` — it proceeds to add weight 1 again (`totalWeight=2`).
5. `totalWeight (2) >= threshold (2)` returns `true`/`DATA_FALSE` incorrectly flips to success (`dataOne()`), even though only one distinct authorized signer actually approved.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1086-1107)
```java
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

```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L242-268)
```java
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
```
