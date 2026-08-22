### Title
Double-counting of a single signer's weight in `ValidateMultiSign` precompiled contract due to malleable-signature bypass of the duplicate-signer check - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract (address `0x0000...1010`, reachable from any smart contract via a `staticcall`/`call`) tallies signer weight by iterating over an attacker-supplied array of signatures and summing `TransactionCapsule.getWeight(permission, recoveredAddr)` for each entry. Its de-duplication logic only rejects an entry if the *exact byte-identical signature* has already been counted; it does **not** reject a second, distinct-but-valid signature that recovers to the *same address*. Because ECDSA signatures are malleable (a signature `(r,s)` and `(r, n-s)` both validate for the same key/message, and recovery id can also vary), a single private key can be used to produce two different valid signature byte strings for the same hash. Submitting both lets one signer's weight be counted twice, mirroring the `ElectionCommission.tallyProposalResult` bug where re-using one delegate address multiple times inflates the tally and creates false consensus.

### Finding Description
`ValidateMultiSign.execute` (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java:1051-1120`) loops over `signatures` extracted directly from calldata: [1](#0-0) 

For each signature it:
1. Recovers `recoveredAddr` from the signature and hash.
2. Checks `ByteArray.matrixContains(executedSignList, recoveredAddr)` — if the address was already seen, it only `continue`s (skips) when the *exact signature bytes* were already recorded (`ByteArray.matrixContains(executedSignList, sign)`); otherwise it merely calls `MUtil.checkCPUTime()` and falls through.
3. Looks up `weight = TransactionCapsule.getWeight(permission, recoveredAddr)` and unconditionally does `totalWeight += weight`, then records both `sign` and `recoveredAddr` into `executedSignList`.

The intent of the address-presence check is clearly to prevent one key from being counted twice, but the implementation only blocks *identical* signature bytes, not *distinct* signatures that recover to the same address. This is the same defect class described in the ElectionCommission report: the tally loop iterates over an unvalidated list keyed by identity and adds weight per iteration without enforcing "one unique identity contributes at most once" — a duplicate identity (delegate address / signer address) is trivially reachable by supplying different raw bytes representing the same underlying signer.

For contrast, the legacy transaction-level signature check `TransactionCapsule.checkWeight` (used for on-chain transaction multi-sig, not reachable from a smart contract) *does* correctly deduplicate by recovered address using a `HashMap` keyed on the encoded address, throwing `"has signed twice!"` if the same address appears twice regardless of signature bytes: [2](#0-1) 

This shows the codebase's own intended invariant ("one signer, one weight contribution") is properly enforced in the transaction path but is defectively re-implemented (address+signature-bytes matching instead of address-only matching) in the TVM precompiled contract path.

### Impact Explanation
`ValidateMultiSign` is used by smart contracts to verify TRON multi-signature permissions on-chain (e.g., for contract-controlled wallets or DApps gating actions behind an account's `Active`/multi-sig permission). If an attacker controls one key that is a member of a multi-sig `Permission` with `threshold > 1`, they can potentially satisfy the threshold alone by submitting two distinct valid signatures from that single key (exploiting ECDSA malleability), causing the contract to treat the call as approved by multiple independent signers when only one real signer authorized it. This is an unauthorized-account-operation / broken access-control condition analogous to the "false consensus" impact in the original report — a single actor can bypass the intended multi-party threshold for on-chain, contract-gated actions.

### Likelihood Explanation
Exploitability depends on whether TRON's ECDSA signing/verification enforces canonical low-`s` values and a single recovery id (many Ethereum-derived stacks do enforce "low-s" canonicalization at the transaction level, but this check would need to be present specifically in the `recoverAddrBySign`/`SignUtils` path used inside `ValidateMultiSign`). I was not able to confirm within the available tool budget whether canonical-signature enforcement exists in this exact TVM precompile code path, so likelihood is uncertain pending that verification. If no canonical-signature enforcement exists here, exploitation only requires deriving a second valid `(r, n-s)` variant of an already-known signature — computationally trivial, no privileged access needed, and reachable by any contract caller via a plain `call`/`staticcall`.

### Recommendation
In `ValidateMultiSign.execute`, deduplicate strictly by `recoveredAddr` (not by the combination of address + raw signature bytes) before adding to `totalWeight`, mirroring the address-only dedup already used in `TransactionCapsule.checkWeight`. Additionally, enforce canonical ECDSA signature form (single valid `s` in the lower half of the curve order, single recovery id) in the underlying signature-recovery utility used by this precompile, so that only one canonical signature is ever considered valid per (key, message) pair.

### Proof of Concept
Conceptual sequence (concrete PoC requires confirming signature-malleability behavior of `SignUtils`/`recoverAddrBySign`, which was not verifiable within the available investigation):
1. Deploy/target an account with an `Active` permission containing two keys `K1` (weight 1) and `K2` (weight 1), threshold 2.
2. Attacker controls only `K1`.
3. Attacker computes the standard signature `sig1 = sign(hash, K1)` and derives the malleable counterpart `sig2 = (r, n-s, alternate recovery id)` for the same `hash`/`K1`.
4. Attacker calls the `ValidateMultiSign` precompile (address `0x...1010`) with `signatures = [sig1, sig2]`.
5. In the loop at `PrecompiledContracts.java:1088-1106`, `sig1` recovers to `K1`'s address and is added with weight 1; `sig2` also recovers to `K1`'s address, but since `sig2`'s raw bytes differ from `sig1`, the `matrixContains(executedSignList, sign)` check fails and weight is added a second time — `totalWeight` reaches 2, meeting the threshold with only one real signer. [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1086-1109)
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

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L233-269)
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
```
