## Analysis

The reported bug class — signature-hash computation that omits the chain ID, enabling a signature valid on one chain to be replayed on another — has a direct analog in java-tron's `ValidateMultiSign` precompiled contract.

### Title
Missing chain-id domain separation in `ValidateMultiSign`/`BatchValidateSign` precompiles allows cross-chain signature replay - (`File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
`ValidateMultiSign` (precompile address `0x...0a`) and `BatchValidateSign` (`0x...09`) are TVM precompiled contracts that any deployed smart contract can invoke to verify off-chain, permission-weighted multisig approvals. The message hash they recover signatures against is built purely from caller-supplied `address`, `permissionId`, and `data`, with no chain identifier mixed in.

### Finding Description
In `ValidateMultiSign.execute()`, the hash used for ECDSA recovery is computed as: [1](#0-0) 

`combine = address || permissionId || data`, hashed with `Sha256Hash.hash(...)` — no chain ID, network magic, or contract-address-of-verifier binding beyond the caller-supplied `address`/`permissionId`. The recovered signer is then checked against the on-chain `Permission` of that `address`: [2](#0-1) 

The precompile is reachable from any `TriggerSmartContract` call once `allowTvmSolidity059` is active: [3](#0-2) 

`BatchValidateSign` has the identical pattern — hash is taken verbatim from caller input with zero chain binding: [4](#0-3) 

This mirrors the `Trust.isValidSignature()` issue in the report: a signature that a dApp's off-chain multisig authority produces to authorize an on-chain action (e.g., releasing funds, approving a permissioned operation) for a given `address`/`permissionId`/`data` triple will validate identically on any other TRON-protocol chain where that same account address and permission configuration exist (mainnet, Nile/Shasta testnets, or any private/forked TRON network sharing the same account state, e.g., from a genesis snapshot or migration). Because the account's `Permission` structure (keys/threshold) is user/dApp controlled and portable across deployments, and the hash carries no chain-specific salt, a signature authorized for use on chain A can be replayed verbatim on chain B to satisfy the same `ValidateMultiSign` check.

### Impact Explanation
Any contract built on top of `ValidateMultiSign`/`BatchValidateSign` as an authorization oracle (e.g., multisig wallets, bridges, escrow releasing TRC10/TRC20 assets, game backends) is exposed to cross-chain signature replay if the same signer keys/accounts and contract logic are deployed on more than one TRON-compatible network. An attacker who obtains a valid multisig-approval signature on one network (e.g., a testnet, a forked chain, or a chain that later hard-forks/splits) can replay it on another network to authorize the same operation there without new consent from the signers — potentially causing unauthorized asset transfers or state changes on the replayed chain.

### Likelihood Explanation
Exploitability depends entirely on external, unprivileged dApp design: it requires (a) a contract using this precompile for signature-gated logic, and (b) that contract/account being deployed or replicated with the same addresses/permissions on more than one TRON-based network. This is a realistic scenario for TRON given its history of testnets (Nile, Shasta) and third-party forks, but the precompile itself behaves like a generic `ecrecover`-style primitive — domain separation (including chain ID) is conventionally the responsibility of the calling contract's `data` payload, not the primitive. This lowers likelihood of node-level/protocol-level impact compared to the reported case, where the vulnerable hashing logic lived inside the application contract itself rather than a generic verification primitive.

### Recommendation
Document explicitly (and consider enforcing) that consumers of `ValidateMultiSign`/`BatchValidateSign` must include a chain identifier (`block.chainid` or equivalent) inside the `data`/`hash` they pass to the precompile, so authorization signatures are bound to a specific chain. Optionally, mix `block.chainid` into the `combine` buffer inside `ValidateMultiSign.execute()` itself to provide chain-binding by default, matching the report's recommendation to hash the chain ID alongside signature data.

### Proof of Concept
1. Deploy identical contract `C` (address `A`) with identical `Permission` (keys `K1`, `K2`, threshold `2`) on both TRON mainnet and a TRON-compatible testnet/fork sharing the same account state.
2. Off-chain, `K1`/`K2` sign `hash = sha256(A || permissionId || data)` to authorize an action via `ValidateMultiSign` on mainnet.
3. Submit the same signatures + `data` to the same `ValidateMultiSign` call on the testnet/fork network — `execute()` recomputes the identical hash (no chain ID mixed in) and the signatures recover to `K1`/`K2`, satisfying the threshold and returning `dataOne()`, letting the attacker replay the authorized action on the second chain without new consent.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L257-259)
```java
    if (VMConfig.allowTvmSolidity059() && address.equals(validateMultiSignAddr)) {
      return validateMultiSign;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1062-1064)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1163)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();
```
