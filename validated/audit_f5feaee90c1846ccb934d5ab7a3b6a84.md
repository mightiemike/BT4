### Title
`PrecompiledContracts.ValidateMultiSign`/`BatchValidateSign` compute signature-verification hashes with no chain-domain separator, enabling cross-chain replay of TVM multisig authorizations - (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The TVM precompiled contracts `ValidateMultiSign` (address `0x0a`) and `BatchValidateSign` (address `0x09`) let any smart contract verify off-chain ECDSA signatures against an on-chain account's `Permission` weight threshold. The message hash they build never incorporates a chain/network identifier, only the target account address, permission id, and caller-supplied payload bytes. This mirrors the H-10 root cause exactly: a "receiving" verification routine binds signatures only to data that can be identical across two independently operating chains, so a signature set authorized for use on one deployment is equally valid on another.

### Finding Description
`ValidateMultiSign.execute` builds the verification hash as: [1](#0-0) 

and then checks recovered signer weight against the `Permission` stored for `address`/`permissionId` on the local chain's account store: [2](#0-1) 

`BatchValidateSign.doExecute` similarly recovers signer addresses from a caller-supplied `hash` word with zero chain binding: [3](#0-2) 

Both precompiles are reachable by any unprivileged user contract via `TriggerSmartContract`/`CreateSmartContract`, exactly the class of "smart-contract-level cross-chain settlement" logic the external report targets. Application contracts (bridges, multisig vaults, custody wallets) commonly build on these precompiles to gate privileged operations (fund release, minting, withdrawal) on collected off-chain validator/owner signatures, using the TRON account `Permission` mechanism as the source of truth for signer weights, exactly like `ChakraSettlement` uses `signature_verifier.verify` gated by validator sets.

Because the hash formula `sha256(address ‖ permissionId ‖ data)` (for `ValidateMultiSign`) contains no chain id, genesis hash, or verifying-contract address component, and because java-tron is routinely forked/redeployed as independent networks (mainnet, Nile, Shasta, and numerous private/enterprise java-tron-based chains) that can end up with the same account address and identical `Permission` (same keys, same threshold, same `permissionId`) — e.g. an account created via the same private key or deterministic derivation on multiple such networks — a set of signatures collected/authorized for use against a contract on chain A satisfies the exact same verification on chain B. Any dApp contract layering privileged logic on top of these precompiles inherits a cross-chain signature-replay primitive identical in kind to the reported `ChakraSettlement` bug: the verification step never asserts "this signature set is scoped to my chain."

### Impact Explanation
An application contract that uses `ValidateMultiSign`/`BatchValidateSign` to authorize privileged state transitions (e.g., release of custodied TRX/TRC20, execution of a multisig-controlled action, bridge withdrawal) can have those authorizations replayed on any other java-tron-based network where the same account address and permission configuration exist. This can lead to duplicate/unauthorized fund releases or state changes on a second chain using signatures only intended for the first — an asset/accounting-corruption impact analogous to the reported High severity finding, though the blast radius depends on which dApp business logic is built atop the precompile.

### Likelihood Explanation
Exploitation requires an application-level design (multisig wallet/bridge contract) built on top of the precompile plus the same account/permission configuration existing on two java-tron-derived networks — a realistic scenario given how common forked/private java-tron deployments and shared-keypair deterministic account setups are. The precompile itself provides no chain-domain separation, so the vulnerability is purely core-code root cause; whether it is triggered depends on downstream contract usage, which is outside core scope but directly enabled by this gap.

### Recommendation
Incorporate a chain-domain separator into the hash computed by `ValidateMultiSign`/`BatchValidateSign` (e.g., mix in a `chainId`/genesis-block hash accessible via `Repository`/`ChainBaseManager`), or document/require callers to embed such a value into the `data` they hash before calling the precompile, and enforce it at the precompile layer so identical `(address, permissionId, payload)` tuples cannot verify successfully across distinct java-tron networks.

### Proof of Concept
1. Deploy the same account key material (or a deterministically-derived account with identical `Permission`: same keys, same `threshold`, same `permissionId`) on two independent java-tron-based networks, Chain A and Chain B.
2. Deploy a bridge/multisig contract with identical bytecode on both chains, calling `ValidateMultiSign` at address `0x0a` to gate a privileged action (e.g., withdrawal) using `(address, permissionId, payload)`.
3. Collect signatures on Chain A authorizing a specific `payload` (e.g., "release funds to X"), verified via `PrecompiledContracts.ValidateMultiSign.execute` as shown at: [4](#0-3) 
4. Submit the identical `(address, permissionId, payload, signatures)` to the corresponding contract call on Chain B. Since the hash and the on-chain `Permission` (weights) are identical, `TransactionCapsule.getWeight`/threshold check succeeds and the privileged action fires on Chain B as well, even though the signers never intended to authorize an action there.

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1187)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[1].intValueSafe() / WORD_SIZE].intValueSafe();
        int addrArraySize = words[words[2].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE || addrArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }

      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[1].intValueSafe() / WORD_SIZE, data) :
          extractBytesArray(words, words[1].intValueSafe() / WORD_SIZE, data);
      byte[][] addresses = extractBytes32Array(
          words, words[2].intValueSafe() / WORD_SIZE);
      int cnt = signatures.length;
      if (cnt == 0 || cnt > MAX_SIZE || signatures.length != addresses.length) {
        return Pair.of(true, DATA_FALSE);
      }
      byte[] res = new byte[WORD_SIZE];
      if (isConstantCall()) {
        //for constant call not use thread pool to avoid potential effect
        for (int i = 0; i < cnt; i++) {
          if (DataWord
              .equalAddressByteArray(addresses[i], recoverAddrBySign(signatures[i], hash))) {
```
