## Analysis

The reported bug class is: a signature scheme binds identity/authorization data (name, version, address, chain) but omits a **domain separator** tying the signature to the specific deployment/chain, enabling the same signature to be replayed elsewhere.

The closest reachable analog in java-tron is the `ValidateMultiSign` TVM precompiled contract, which computes the signed hash as `sha256(address || permissionId || data)` with no chain-binding component, then recovers signer addresses and checks them against the target account's on-chain `Permission`.### Title
Missing domain separator in `ValidateMultiSign` TVM precompile enables cross-chain signature replay - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract (exposed at a fixed system address and callable by any TVM contract) authorizes an action by recovering signer addresses from a hash built solely from `address || permissionId || data`. This hash contains no chain-binding component (no chain id, genesis hash, or contract-domain value), so any signature that authorizes an action for a given account/permission on one TRON-compatible network is equally valid on any other network sharing the same account address and permission configuration — mirroring the CPortModule finding where the signature domain (name/version/address/chain) was never asserted against the actual deployment context.

### Finding Description
`ValidateMultiSign.execute` builds the signed hash as: [1](#0-0) 

`address`, `permissionId`, and the caller-supplied `data` are the only inputs mixed into the hash — there is no chain identifier, genesis block hash, or any other value that binds the signature to a specific chain/deployment. The recovered signer is then checked purely against the current on-chain `Permission` weight/threshold for that account: [2](#0-1) 

This precompile is the canonical TVM building block that Solidity contracts (wallets, meta-transaction relayers, exchange/settlement contracts, escrow, etc.) use to accept off-chain-authorized approvals instead of requiring an on-chain-signed transaction. TRON's ecosystem includes multiple independently operating chains that share the exact same address-derivation scheme and key format (mainnet, testnets such as Nile/Shasta, and third-party TRON-protocol forks/sidechains). If the same private key (hence the same address) has the same or a similarly-configured `Permission` on two such chains — a common scenario for early-access/test deployments, cross-chain bridges, or forks seeded from the same genesis/account set — a signature captured on chain A remains fully valid when replayed against the same contract logic deployed on chain B, because nothing in the hash construction changes across chains.

This is structurally identical to the CPortModule issue: the signature's "domain" (contract/chain context) is never asserted to match the actual deployment/verification context, so a signature is not scoped to the chain it was intended for.

### Impact Explanation
Any contract built on top of `ValidateMultiSign` for authorization (e.g., gasless approvals, multi-sig wallets, escrow releases, exchange withdrawal approvals) inherits this replay weakness. An attacker who obtains a validly-signed authorization for account/permission `P` on chain A can resubmit the identical `(address, permissionId, data, signatures)` tuple against the same precompile on chain B (or against a redeployed/forked copy of the same DApp) and obtain the same "authorized" result, driving unauthorized state changes/asset movements on the second chain. This is a concrete authentication/replay impact, consistent with the "invalid signature replay" impact class.

### Likelihood Explanation
Exploitability depends on the same account address/permission existing with signable weight on more than one chain instance that runs this precompile logic (which is a realistic occurrence given TRON's protocol is forked for numerous sidechains/testnets and users frequently reuse the same keys across them), and on an application built on `ValidateMultiSign` not independently adding its own chain-binding nonce/domain in the `data` parameter. Because the domain gap is at the protocol/precompile level rather than something individual DApp authors are prompted to fix, it is likely that some downstream consumers of this precompile omit chain-scoping, making this a systemic — not merely theoretical — replay surface.

### Recommendation
Mix a chain-binding value (e.g., the current `chainId`/genesis block hash already exposed to the VM via the `CHAINID` opcode path in `actuator/src/main/java/org/tron/core/vm/program/Program.java`) into the `combine` buffer used by `ValidateMultiSign` before hashing, so recovered signatures are cryptographically scoped to the chain on which the precompile executes: [3](#0-2) 
This closes the domain gap analogous to adding `domainSeparator` in the referenced `PaymentProcessor.sol` fix.

### Proof of Concept
1. Deploy identical DApp logic (or observe that the same account key already exists) on TRON chain A and a second TRON-protocol chain/fork B that shares the same account-address derivation.
2. On chain A, obtain a signature `sig` over `hash = sha256(address || permissionId || data)` from a key holding sufficient `Permission` weight for `address`/`permissionId` (as constructed in `ValidateMultiSign.execute`, lines 1057-1064).
3. Call the same contract's `validatemultisign(address, permissionId, data, [sig])` on chain B with the identical parameters.
4. Because the hash formula never incorporates any chain-specific value, `ValidateMultiSign` on chain B recovers the same signer address, finds the same (or equivalently-configured) `Permission`, and returns `DataWord.ONE` — authorizing the action on chain B using a signature that was only ever intended for chain A.

### Citations

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
