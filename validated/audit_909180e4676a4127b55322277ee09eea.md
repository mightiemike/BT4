Based on the investigation, the closest analog in java-tron to the reported "hash missing chain-id" replay bug is the `ValidateMultiSign` TVM precompiled contract, which computes its signature-verification hash purely from `(address, permissionId, data)` with no chain-specific domain separator.### Title
Cross-chain/cross-network Replay in `ValidateMultiSign` Precompiled Contract Due to Missing Chain-Binding in Signature Hash - (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The TVM precompiled contract `ValidateMultiSign` (used by smart contracts to verify off-chain multisig authorizations against an on-chain account `Permission`) builds its signature-verification hash solely from `address || permissionId || data`, with no chain-specific domain separator such as a chain/genesis identifier. Since java-tron account addresses and `Permission` structures are chain-agnostic (the same private key/address/permission config can exist identically on TRON Mainnet, Nile/Shasta testnets, or any private/enterprise fork of the java-tron client), a signature produced for one chain can be replayed against any contract deployed with the same address/permission layout on a different TRON-compatible network.

### Finding Description
`ValidateMultiSign.execute()` computes:
```
byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
byte[] hash = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), combine);
``` [1](#0-0) 

and then recovers signer addresses from this `hash` to compute the total weight against the account's `Permission` threshold: [2](#0-1) 

This is exactly the bug class in the external report: the hash used to authenticate an off-chain signature omits any value that binds it to a specific chain (no `chainid`, no genesis hash, no network identifier). The `data` field is caller-supplied and entirely opaque to the precompile — nothing in the precompile itself enforces or suggests domain separation.

Because java-tron address derivation (ECDSA public key → address) and `Permission`/`Key` structures are identical across all java-tron-based networks (mainnet, Nile, Shasta, and any privately operated fork/clone of the client), a user who controls the same private key on two different networks will have the same `address`. If a dApp (e.g., an escrow, marketplace, or bridge-like contract) is deployed with the same bytecode/logic on two such networks and calls `ValidateMultiSign` with the same `permissionId` and same `data` (e.g., an order id, withdrawal request, or other application payload that a naive integrator does not additionally salt with a chain identifier), a signature collected for one network is valid and replayable on the other network.

This mirrors the reported `_borrowHash` issue precisely: the fix recommended there — folding `block.chainid` into the signed hash — has no counterpart at all in `ValidateMultiSign`; the precompile provides zero help to callers wanting cross-chain safety, and unlike a typical EIP-712 domain, there is no reserved/enforced field for it.

### Impact Explanation
Any TVM contract that relies on `ValidateMultiSign` for authorization of state-changing actions (fund transfers, order execution, permission-gated operations) is exposed to replay of a legitimately-signed authorization across any other TRON-compatible network sharing the same account/permission state, when the calling contract does not independently embed a chain identifier into `data`. This can result in unauthorized execution of a previously-authorized action on a second network — i.e., unauthorized account operation / asset movement — without the signer's fresh consent on that network.

### Likelihood Explanation
Exploitation requires: (1) a dApp built on `ValidateMultiSign` for its own authorization scheme, (2) the same contract/bytecode (or logic producing the same `data` hash) deployed on two java-tron-based networks, and (3) the signer's account/permission existing identically on both. This is a realistic scenario for TRON given its ecosystem of testnets (Nile, Shasta) and forkable open-source client used to launch private/enterprise chains, where developers commonly reuse the exact same contracts and off-chain signing flows across environments. The vulnerability is entirely reachable by an ordinary user submitting a `TriggerSmartContract` transaction — no privileged access is required — but requires an integrating dApp that fails to add chain-domain separation itself, since the precompile is a generic primitive similar to `ecrecover`.

### Recommendation
Add an explicit chain/network binding to the `ValidateMultiSign` hash computation so replay protection does not depend entirely on integrator discipline, e.g.:
```java
byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data,
    chainIdBytes /* derived from genesis block hash or a configured network id */);
byte[] hash = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), combine);
```
At minimum, document prominently that `data` passed to `ValidateMultiSign` must include a network/chain identifier, and consider exposing a chain-id/genesis-hash accessor in the TVM (already partially present for other opcodes) so contract authors can enforce this without ambiguity.

### Proof of Concept
1. Deploy an identical dApp contract `C` on TRON Mainnet and on a second TRON-based network (e.g., Nile testnet, or a private fork), where `C` calls `ValidateMultiSign(address, permissionId, data, sigs)` to authorize a withdrawal keyed only by an application-level `data` value (e.g., `keccak256(orderId, amount, recipient)` with no chain id included).
2. A user with private key `k` has address `A` on both networks (same address, since address derivation from `k` is chain-agnostic) and configures an identical `Permission` (`permissionId`) on both networks.
3. The user signs `hash = Sha256Hash(A || permissionId || data)` intending it for Network 1 only, and submits it to `C` on Network 1, authorizing a withdrawal.
4. An attacker who observes this signature (e.g., from a public transaction/calldata) submits the same `(address, permissionId, data, sigs)` tuple to `C` deployed on Network 2.
5. `ValidateMultiSign` on Network 2 recomputes the identical `hash` (since it depends only on `address`, `permissionId`, `data`, none of which differ across networks) and successfully recovers the same signer weight, authorizing the same action on Network 2 without the user's fresh consent for that network.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1058-1064)
```java
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1110)
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
```
