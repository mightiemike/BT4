### Title
Cross-chain Signature Replay in `ValidateMultiSign` TVM Precompiled Contract - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` precompiled contract (address `0x...a`) builds the message hash that off-chain signers sign entirely on-chain from `address`, `permissionId`, and an arbitrary `data` blob, with no chain identifier, contract address, or expiration timestamp mixed in. Any signature produced to satisfy a multi-sig threshold for one TRON-compatible network (mainnet, a testnet, or any private/forked TVM chain sharing the same account address and keys) remains valid forever and can be replayed verbatim on any other network running the same contract logic, mirroring exactly the cross-chain signature replay bug class described in the external report (missing chain ID / expiration in a signed message consumed by on-chain verification logic).

### Finding Description
`ValidateMultiSign.execute` constructs the signed hash internally as: [1](#0-0) 

`combine` is only `address || permissionId || data` — it contains no chain ID, no genesis/block hash, no contract address, and no expiration field. The resulting `hash` is what off-chain co-signers are expected to sign, and the recovered signer addresses are weighed against the account's `Permission` to decide whether the threshold is met: [2](#0-1) 

Because this hash is completely deterministic from data that is identical across networks (the same TRON account, permission id, and payload), a set of co-signers' signatures collected to authorize an action on one TVM-based chain can be resubmitted unchanged to any other chain (e.g., mainnet vs. Nile/Shasta testnet, or a private/forked TVM chain) where the same account address and permission structure exist, satisfying the same threshold check there. This is invoked from ordinary contract bytecode via `CALL` to the precompile address, i.e. reachable by any user-submitted contract transaction — no privileged role is required to trigger `execute`; only the party relying on the multisig result (a dApp contract) is affected.

### Impact Explanation
Any smart contract built on TRON that uses `ValidateMultiSign` as an authorization gate for sensitive on-chain operations (e.g., releasing funds, approving a withdrawal, executing a governance action) can have a previously-collected valid signature set replayed on a different TRON-compatible network to force the same authorized action there, without the signers' fresh consent for that specific network/deployment. This corresponds to unauthorized account/contract operation and asset/accounting corruption categories, since it bypasses the intended one-time/one-network authorization semantics that contract authors reasonably assume the built-in precompile provides.

### Likelihood Explanation
Exploitation requires that the same account address (and its permission-holding keys) exists and is used to satisfy multisig thresholds identically across two TVM-compatible deployments — a realistic scenario for exchanges, custodians, or DAOs that mirror the same accounts/keys across mainnet and other TRON-derived networks (testnets, private chains, or future L2/sidechain deployments sharing TRON's VM). No dynamic component (chain id, contract address, expiry) is present in the hash, so any signature collected once is valid forever and everywhere the same account/permission is deployed, making the risk concrete once such multi-network reuse occurs.

### Recommendation
Include a chain/network identifier (and ideally the target contract address and an expiration timestamp) as part of the `combine` byte sequence hashed in `ValidateMultiSign.execute`, following an EIP-712-like domain-separation approach, so that signatures are bound to a single chain and cannot be replayed across independently-operated TVM-compatible networks. At minimum, document explicitly in the precompile's documentation that callers must incorporate chain-binding data into `data` themselves, since the hash construction alone provides no cross-chain protection.

### Proof of Concept
1. Deploy an identical account (same address/keys) with an Active permission (threshold `t`, signers `S1..Sn`) on Chain A (e.g., mainnet) and Chain B (e.g., a TRON-derived testnet or private fork).
2. On Chain A, a contract calls `ValidateMultiSign(address, permissionId, data, signatures[])` to authorize an operation; signers `S1..St` produce signatures over `sha256(address || permissionId || data)` as computed at [3](#0-2) .
3. An attacker (or the operation initiator) submits the exact same `address`, `permissionId`, `data`, and `signatures[]` to the equivalent contract deployed on Chain B.
4. Because the hash formula has no chain-specific input, `recoverAddrBySign` recovers the same valid signer addresses at [4](#0-3) , the threshold check passes again on Chain B, and the sensitive operation executes there without the signers ever intending or re-authorizing it for Chain B.

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
