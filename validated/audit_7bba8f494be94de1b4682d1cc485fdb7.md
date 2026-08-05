## Title
Untyped/undomain-separated signature hashing in the `validatemultisign` TVM precompile enables cross‑contract, cross‑chain and cross‑purpose signature replay - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract, reachable by any unprivileged smart contract via the TVM opcode dispatch, authorizes off‑chain user signatures by hashing only `(address, permissionId, data)` with no chain identifier, no reference to the calling contract, and no type/purpose tag. This is the same root cause flagged in the Rigor "Untyped data signing" report: application-level signatures are hashed and verified without an EIP‑712-style domain separator, so a signature produced for one contract, chain, or purpose can be accepted by another.

### Finding Description
`ValidateMultiSign.execute` builds the signed message as: [1](#0-0) 

```
byte[] address = words[0].toTronAddress();
int permissionId = words[1].intValueSafe();
byte[] data = words[2].getData();

byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
byte[] hash = Sha256Hash.hash(..., combine);
```

`data` is an arbitrary 32-byte value fully controlled by the calling contract/user - there is no built‑in binding to:
- the calling contract's own address (the contract that actually consumes the authorization),
- the chain ID (TRON mainnet vs. Nile/Shasta testnets vs. TRON-compatible sidechains that reuse the same account/permission model), or
- a type hash identifying what the signature is authorizing.

This mirrors exactly the Rigor pattern where `Community.sol`/`Project.sol` hash raw application parameters without a domain separator. Any Solidity contract built on top of this precompile (a very common pattern for gasless multisig-approval flows on TRON) inherits the same weaknesses documented in the source report:

1. **Cross-contract replay** - if two different consuming contracts derive the same `(address, permissionId, data)` triple (plausible whenever `data` is a generic value such as a bare hash of an amount/nonce, without contract-specific salting), a signature intended for contract A validates equally for contract B, exactly as demonstrated by the `inviteContractor`/`setComplete` cross-function reuse example in the report.
2. **Cross-chain replay** - because chain identity is absent from the hash, a signature collected on one TRON-based network is valid on any other network sharing the same account and permission state, mirroring the Ethereum/Polygon replay scenario in the report.
3. **Phishing/format collision** - the payload shape `(address, uint256 permissionId, bytes32 data)` is generic enough that a signature harvested by an unrelated dApp using a structurally compatible signing flow could be replayed into a victim's `validatemultisign`-protected contract, just as the report warns about generic ECDSA payloads being reused across unrelated Ethereum applications.

The companion `BatchValidateSign` precompile is even more permissive: it verifies raw caller-supplied `hash = words[0].getData()` against arbitrary addresses with no domain separation at all. [2](#0-1) 

The intended usage pattern - hashing `(address, permissionId, data)` and having off-chain keys sign it - is exercised directly in the test suite, confirming this is the sanctioned, documented way dApp authors are expected to use the precompile: [3](#0-2) 

### Impact Explanation
Any TVM contract that relies on `validatemultisign`/`batchvalidatesign` as an authorization primitive for user-signed approvals (a common gasless-approval / meta-transaction pattern on TRON) inherits a signature scheme without contract-, chain-, or purpose-binding. A malicious contract or actor can replay a legitimately-obtained signature into an unrelated multisig-protected contract, across TRON networks, or for a different action than the signer intended, potentially resulting in unauthorized approvals or fund movement in whichever dependent contract accepts the replayed signature. This is a state/accounting and authorization-bypass class impact realized in TVM-reachable, unprivileged-user code.

### Likelihood Explanation
Medium. Exploitation requires either (a) two independently-deployed contracts that both consume `validatemultisign` and happen to produce colliding `(address, permissionId, data)` triples for different purposes, or (b) the same account/permission being reused across multiple TRON-compatible networks. Because the precompile itself provides no guardrails, the burden of building a safe domain separator falls entirely on individual dApp developers, and the precompile's own test suite and documentation do not demonstrate or enforce any domain separation - making misuse likely across the ecosystem of contracts that build on this primitive.

### Recommendation
Follow the same EIP-712-style mitigation recommended in the source report: extend the hash construction inside `ValidateMultiSign`/`BatchValidateSign` to mandatorily include a domain separator composed of the chain ID (or genesis block hash), the address of the calling contract (`msg.sender` at the TVM level), and a type hash describing the purpose of the signed data, rather than leaving `data` fully opaque and developer-supplied. At minimum, update documentation to explicitly require callers to embed chain ID and contract address inside `data`, and add defensive precompile-level enforcement where feasible.

### Proof of Concept
Using the test harness structure in `ValidateMultiSignContractTest`:
1. Contract A computes `dataA = sha256(orderId)` and calls `validatemultisign(owner, permissionId, dataA, sigs)` where `sigs` are off-chain signatures over `sha256(owner || permissionId || dataA)`.
2. Contract B (unrelated dApp, different purpose, e.g. a withdrawal approval) independently derives the identical 32-byte value for its own `data` parameter (e.g., also just `sha256(orderId)` if both apps use the same ID scheme) and calls `validatemultisign(owner, permissionId, dataA, sigs)` with the *same* `sigs` array captured from Contract A's flow.
3. Because the underlying hash only depends on `(address, permissionId, data)` and never references which contract is asking or which chain it's on, `ValidateMultiSign.execute` returns `DATA_ONE` (success) for Contract B as well - see the combine/hash computation at [4](#0-3)  - authorizing an action the signer never intended to approve for Contract B.

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1177)
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
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L104-121)
```java
    byte[] address = key.getAddress();
    int permissionId = 2;
    byte[] data = Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), longData);

    //combine data
    byte[] merged = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
    //sha256 of it
    byte[] toSign = Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), merged);

    //sign data

    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    //add Repetitive
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    signs.add(Hex.toHexString(key2.sign(toSign).toByteArray()));
```
