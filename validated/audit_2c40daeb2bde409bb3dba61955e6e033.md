### Title
Signature malleability in `ValidateMultiSign`/`BatchValidateSign` precompiles allows a single key to double-count weight when checked-off against raw signature bytes - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
The `ValidateMultiSign` precompiled contract (and the analogous `BatchValidateSign`) implements multi-signature threshold checking for TVM contracts. It deduplicates repeated signers by comparing the raw signature bytes (`sign`), not solely by the recovered address, before deciding whether to add weight again. This mirrors the report's core anti-pattern: using the raw/encoded signature bytes as the identity/uniqueness key for a security check that should instead be keyed on the semantic content actually being signed (here, the signer's address/identity), which is vulnerable to ECDSA signature malleability.

### Finding Description
In `ValidateMultiSign.execute` [1](#0-0) , for each supplied raw signature the code recovers the signer address via `recoverAddrBySign(sign, hash)`, and then builds `sign = merge(recoveredAddr, sign)` to track "already used" (address, signature-bytes) pairs in `executedSignList`:

```
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  if (ByteArray.matrixContains(executedSignList, sign)) {
    continue;
  }
  MUtil.checkCPUTime();
}
long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
...
totalWeight += weight;
executedSignList.add(sign);
executedSignList.add(recoveredAddr);
```

The intent is clearly to prevent a single signer's weight from being counted more than once when the *same* signature is supplied multiple times (e.g., padding attacks). However, the dedup check only `continue`s (skips adding weight) when the *exact byte-for-byte signature* has already been recorded for that address. If the address has been seen before but the signature bytes differ, the code does not skip — it only calls `MUtil.checkCPUTime()` (a CPU/time guard) and then proceeds to compute and add the signer's weight again.

Standard ECDSA signatures are malleable: for any valid signature `(r, s)` over a given message/hash, `(r, n-s)` (with a correspondingly flipped recovery id `v`) is also a valid signature that recovers to the exact same address. `recoverAddrBySign` uses `Rsv.fromSignature` + `SignUtils.fromComponents(...).validateComponents()` before recovery; there is no evidence in the reachable code that a canonical low-S form is being enforced at this call site (unlike the OpenZeppelin `ECDSA` library referenced in the report, which explicitly checks for malleable/high-S signatures). As a result, an attacker holding one valid signature from a single permission key can trivially derive a second, byte-distinct signature `(r, n-s)` for the same message that still recovers to the same signer address. Supplying both in the `signatures` array passed to `ValidateMultiSign` causes the loop to add that signer's `weight` twice toward `totalWeight`, because the raw-signature-bytes equality check fails to recognize the two as "the same approval."

This is the same bug class as the report: relying on comparing the signature encoding (or a hash of it) as a substitute for the signer's identity/intent, rather than deduplicating solely on the recovered address (the actual semantic fact being checked — "has this key already approved?").

### Impact Explanation
If exploitable, this allows a single controlling private key behind one `Permission` key entry to be counted as if it produced two (or more, via additional malleable variants) independent approvals. For multisig-style TVM contract permissions where `permission.getThreshold()` is meant to require distinct signers reaching a cumulative weight, a single signer could artificially inflate `totalWeight` and satisfy the threshold alone, bypassing the intended multi-party authorization control (`account.getPermissionById(permissionId)` / `TransactionCapsule.getWeight`). This is an unauthorized-account-operation-class impact: it can permit an operation gated by multi-signature policy to execute with insufficient actual authorization. [2](#0-1) 

### Likelihood Explanation
The precompile is directly reachable from any TVM contract call (any account can invoke a smart contract that calls the `ValidateMultiSign`/`BatchValidateSign` precompiled address), i.e., reachable from an anonymous broadcast transaction with no privileged access required. Generating a malleable variant of a known/observed ECDSA signature is a standard, cheap operation (flip `s` to `n-s`, adjust `v`). The main open question — which I could not fully confirm given tool/iteration limits — is whether `SignUtils.fromComponents(...).validateComponents()` (in `ECKey`/`SM2` depending on `isECKeyCryptoEngine()`) enforces canonical low-S form and would reject the malleable `(r, n-s)` variant before `recoverAddrBySign` returns an address. If it does enforce canonical S (as OpenZeppelin's `ECDSA` library does, per the report), this specific path is not exploitable and the finding reduces to a defense-in-depth/code-quality concern about keying dedup on raw signature bytes instead of recovered address. I was not able to verify the body of `validateComponents()` before the tool budget was exhausted, so this should be confirmed directly in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` and `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java`.

### Recommendation
- Deduplicate `executedSignList` solely on `recoveredAddr` (the signer identity), not on the combination with raw signature bytes; once an address has contributed weight, any further signature recovering to that same address should be skipped entirely, regardless of its byte encoding.
- Confirm (and, if missing, enforce) that `validateComponents()` in the active `SignatureInterface` implementation rejects non-canonical/high-S signatures, consistent with malleability-resistant ECDSA verification, before this recovered address is trusted for weight accounting.
- Apply the same fix to `BatchValidateSign`, which performs a similar per-signature address recovery/weight-style verification pattern and should not rely on distinguishing signatures by raw bytes rather than recovered address, if used in a similar cumulative-approval context.

### Proof of Concept
1. Attacker controls a private key that is one of the `Key` entries in a target account's `Permission` (with `weight = W`, and `permission.getThreshold() = T > W`, requiring at least one other approver).
2. Attacker signs the canonical `hash = Sha256Hash.hash(address || permissionId || data)` with their key, producing a valid signature `sig1 = (r, s, v)`.
3. Attacker computes the malleable counterpart `sig2 = (r, n-s, v')` (standard secp256k1 malleability transform), which is a valid signature over the same `hash` recovering to the same address.
4. Attacker crafts a call to the `ValidateMultiSign` precompiled contract (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java:1036`) supplying `signatures = [sig1, sig2]` for `address`/`permissionId`/`data`.
5. In `execute`, the first iteration recovers the attacker's address, weight `W` is added, `sign1` (merged address+sig1) is recorded. The second iteration recovers the same address (already in `executedSignList`), but `sign2` (merged address+sig2) does not byte-match `sign1`, so the `continue` is skipped; `checkCPUTime()` runs, then weight `W` is added a second time, making `totalWeight = 2W`.
6. If `2W >= T`, the call returns `dataOne()` (success), even though only a single distinct private key actually approved — violating the intended multisig threshold policy. (Exploitability contingent on `validateComponents()` not rejecting the non-canonical `s`, per the caveat above.) [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1120)
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
