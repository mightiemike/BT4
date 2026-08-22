### Title
Replay of `ValidateMultiSign`/`BatchValidateSign` precompile signatures across contracts and networks due to missing domain separation (`address(this)`, `chainId`, nonce) - (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The TVM precompiled contracts `ValidateMultiSign` (address `0x0a`) and `BatchValidateSign` (address `0x0b`) expose a generic off-chain signature-verification primitive that any deployed smart contract can call to check whether a set of ECDSA signatures satisfy an account's multi-sig permission threshold over an arbitrary `data` blob. Exactly like the zNS `StakingController.createBid` bug, the hash that is actually signed/verified is built only from `(owner account address, permissionId, data)` — it contains no binding to the calling/verifying contract address, the chain id, or a nonce. Any signature a user produces for one contract's `data` payload therefore remains valid for replay against any other contract (or any other TRON-based network/testnet) that reuses the same `(address, permissionId, data)` tuple, since the precompile itself provides no domain separation.

### Finding Description
`ValidateMultiSign.execute` computes the signed hash as: [1](#0-0) 

and `BatchValidateSign` similarly hands the raw caller-supplied `hash` word directly to signature recovery without any additional binding: [2](#0-1) 

Signature recovery itself (`recoverAddrBySign`) only recovers the address from `(sign, hash)` with no context about which contract invoked the precompile, which chain it runs on, or whether this exact signature was already consumed: [3](#0-2) 

This mirrors the zNS finding precisely: the message that is signed is `keccak256/sha256(owner, permissionId, data)` — i.e. it excludes `address(this)` (the verifying/calling contract), `block.chainId`, and a `nonce`. Consequently:
- The same signature is valid for every contract on the TRON mainnet that happens to be called with the same `(owner, permissionId, data)` triple, since the precompile does not incorporate the caller's own contract address into the hash.
- The same signature remains valid across TRON-compatible networks (Mainnet, Nile, Shasta, or any private/side chain forked from java-tron) because there is no chain id in the signed payload.
- There is no on-chain nonce/replay-protection store analogous to the report's recommended "dedicated mapping" — reuse protection is left entirely to whatever calling contract's business logic happens to implement (e.g. an `approvedBids`-style mapping), and the precompile provides no baseline guarantee.

This is a protocol-level primitive (not a single dApp's bug): every TVM contract author who uses `ValidateMultiSign`/`BatchValidateSign` for off-chain approvals (a common documented pattern in TRON for gas-less meta-transactions) inherits this weakness unless they manually add chain/contract/nonce binding into `data` themselves — exactly the missing "dedicated mapping ... plus `address(this)`, `block.chainId`, `registrar`(caller identity) and `nonce`" called out in the original report.

### Impact Explanation
An attacker who obtains (or is given) a validly-signed `(owner, permissionId, data)` triple approved for one contract/purpose can replay it against:
- A different contract on the same chain that expects the same triple (e.g. two DApps sharing an identical off-chain approval schema), causing unauthorized state transitions/asset movement validated as if freshly authorized by the account owner.
- The same contract redeployed or forked onto a different TRON-compatible network, since chain id is not part of the hash — this is directly analogous to the report's "different registrar or different network" replay scenario.

Because `ValidateMultiSign`/`BatchValidateSign` are consensus-level precompiles used by arbitrary smart contracts, the blast radius spans all applications relying on this pattern for signature-gated actions (transfers, approvals, governance votes, etc.), leading to unauthorized account operations / asset corruption when replay assumptions are violated.

### Likelihood Explanation
Exploitability requires that some deployed contract's off-chain signing scheme (the `data` it hashes/signs) collides across two verifying contexts (two contracts, or the same contract on two networks) without contract-specific/chain-specific salt. This is a realistic and common developer mistake pattern (as demonstrated by the very zNS report this analog is based on), and the precompile provides no built-in guardrail, making it likely to recur across the ecosystem of contracts built on `ValidateMultiSign`. The precompile is callable by any TVM contract, reachable by any user broadcasting a `TriggerSmartContract` transaction, so no privileged access is needed to trigger the vulnerable condition — only the presence of a contract whose signing scheme lacks its own domain separation.

### Recommendation
Add mandatory domain separation directly into the precompile's hash computation, rather than leaving it entirely to calling-contract discipline:
- Include the calling contract's address (`msg.sender`/the precompile caller context) in the combined buffer that is hashed in `ValidateMultiSign.execute` and `BatchValidateSign.doExecute`.
- Include `block.chainId` (or the equivalent TRON chain identifier) in the same buffer so signatures cannot be replayed across networks.
- Document/require callers to include a nonce or otherwise unique identifier in `data`, and consider exposing a reference on-chain "used signature" tracking helper so contract authors are not solely responsible for replay-protection storage, mirroring the report's recommended dedicated mapping approach.

### Proof of Concept
1. Deploy two independent smart contracts, `ContractA` and `ContractB`, both of which call the `ValidateMultiSign` precompile (`0x...0a`) to authorize an action, both using the identical `data` encoding scheme for "approve transfer of amount X" (a realistic scenario, since many wallets/SDKs standardize this encoding).
2. An account owner signs `sha256(merge(address, permissionId, data))` intending to authorize the action solely in `ContractA`.
3. Because the hash in `PrecompiledContracts.java:1062-1064` never incorporates the calling contract's address, submit the exact same signature to `ContractB`; `ValidateMultiSign.execute` recovers the same signer address and weight, returning `DATA_ONE`/success, `Pair.of(true, dataOne())` at line 1109, authorizing the action in `ContractB` without the owner's intent.
4. Repeat step 3 against a fork of the chain (e.g. a private/testnet with a different chain id running the same java-tron code) to show the same signature also validates there, since no chain id is included in the hash — completing the cross-network replay scenario described in the original zNS report.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L371-388)
```java
  private static byte[] recoverAddrBySign(byte[] sign, byte[] hash) {
    byte[] out = null;
    if (ArrayUtils.isEmpty(sign) || sign.length < 65) {
      return new byte[0];
    }
    try {
      Rsv rsv = Rsv.fromSignature(sign);
      SignatureInterface signature = SignUtils.fromComponents(rsv.getR(), rsv.getS(), rsv.getV(),
          CommonParameter.getInstance().isECKeyCryptoEngine());
      if (signature.validateComponents()) {
        out = SignUtils.signatureToAddress(hash, signature,
            CommonParameter.getInstance().isECKeyCryptoEngine());
      }
    } catch (Throwable any) {
      logger.info("ECRecover error", any.getMessage());
    }
    return out;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1058-1064)
```java
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1163)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();
```
