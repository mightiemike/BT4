Based on my research, the closest concrete analog reachable from an anonymous smart-contract call is in the `ValidateMultiSign` precompiled contract used by the TVM.

### Title
Cross-chain signature replay in `ValidateMultiSign`/`BatchValidateSign` precompiles due to missing chain-domain binding - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract (invoked via TVM `CALL` by any smart contract, i.e. reachable from an anonymous broadcast transaction) builds the message hash that off-chain signers must sign purely from `address || permissionId || data`, with no chain-specific domain separator (no chain id, genesis-block hash, or contract self-address mixed in).

### Finding Description
`ValidateMultiSign.execute` computes: [1](#0-0) 
```
byte[] address = words[0].toTronAddress();
int permissionId = words[1].intValueSafe();
byte[] data = words[2].getData();

byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
byte[] hash = Sha256Hash.hash(CommonParameter
    .getInstance().isECKeyCryptoEngine(), combine);
```
The recovered signer addresses are then weighed against the account's on-chain `Permission` object and, if the accumulated weight passes the threshold, the precompile returns "valid" [2](#0-1) . `BatchValidateSign` has the same structural weakness: it takes an attacker/caller-supplied raw `hash` directly from calldata and recovers signers against it with no protocol-level binding to anything chain-specific [3](#0-2) .

Because TRON account addresses are derived the same way (secp256k1 → same 0x41-prefixed address) on every java-tron-based deployment (mainnet, Nile/Shasta testnets, private/enshrined side-chains such as BTTC, or any forked chain sharing the same genesis witness/committee keys), a signature produced by a committee/multisig member to authorize `permissionId`+`data` for a particular account on one chain is bit-for-bit valid on every other chain running the same precompile, since `combine` contains no chain-unique salt. This is structurally identical to the reported `PhiFactory::signatureClaim` bug: the framework itself constructs the signed payload and silently omits a chain-domain field, so any downstream Solidity/TVM contract that trusts this precompile as its sole authorization check (e.g. bridge/custody/DAO contracts using multisig committee approval, which is exactly the pattern TRON promotes for these precompiles) inherits the cross-chain replay flaw without any way to opt out.

### Impact Explanation
Any contract that relies on `ValidateMultiSign` to gate privileged actions (fund releases, cross-chain bridge withdrawals, governance execution) keyed by `(address, permissionId, data)` can be driven into accepting a signature that was legitimately produced for an equivalent action on a different chain instance. This directly enables unauthorized execution of privileged multisig-gated operations and consequent asset/accounting corruption, mirroring the "unauthorized claiming" impact of the original finding.

### Likelihood Explanation
Exploitation requires only: (1) an off-chain signature legitimately collected on chain A for some `(address, permissionId, data)` tuple, and (2) a target contract on chain B using the same `permissionId`/`data` semantics (which is likely when the same dApp/bridge software is deployed identically across TRON mainnet, testnets, or sibling side-chains). No privileged access, leaked keys, or malicious peer/node behavior is needed — an ordinary user submits a normal transaction that triggers a `CALL` to the precompile, exactly like the PoC in the original report reused a signature across chains via a normal contract call path.

### Recommendation
Mix a chain-unique domain value (e.g. the current chain's genesis block id, obtainable via `DynamicPropertiesStore`, or `block.chainid`-equivalent) into the `combine` buffer before hashing in `ValidateMultiSign`, analogous to how `Claimable::signatureClaim` in the reference report substitutes `block.chainid` before re-verifying. For `BatchValidateSign`, since the hash is caller-supplied, at minimum document/enforce that callers must incorporate a chain-domain separator, or add an on-chain check that rejects hashes not bound to the current chain identity when weight-checking against `Permission`.

### Proof of Concept
1. Deploy identical bridge/custody contracts `C` on chain `A` and chain `B` (both java-tron based, sharing genesis committee keys), each using `ValidateMultiSign(address, permissionId, data)` to authorize a withdrawal keyed by `data` (e.g., `withdrawalId`).
2. Committee member signs `sha256(address || permissionId || data)` off-chain to authorize withdrawal `data=X` on chain `A`.
3. Attacker submits a transaction to contract `C` on chain `B` supplying the same `address`, `permissionId`, `data=X`, and the signature obtained in step 2.
4. `ValidateMultiSign.execute` recomputes the identical hash (no chain binding), recovers the same signer, weight check passes, and returns "valid" — contract `C` on chain `B` executes the withdrawal it never legitimately authorized, exactly as in the reported cross-chain replay scenario.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1058-1065)
```java
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1096-1109)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1163)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();
```
