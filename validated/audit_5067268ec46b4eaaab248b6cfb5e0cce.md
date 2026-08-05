### Title
`ValidateMultiSign` precompile computes signed hashes without any contract/platform domain-separator, enabling cross-contract signature replay - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract (address `0x...a`) is java-tron's on-chain analog of Futureswap's `MessageProcessor` meta-transaction verifier: it lets an arbitrary calling smart contract ask "does this set of signatures satisfy an account's permission threshold?" As in the reported Futureswap bug, the hash that is actually signed/verified is built purely from generic, caller-supplied fields — the target account address, the permission id, and an arbitrary `data` blob — with no binding to the specific consuming contract (no `msg.sender`/caller address, no precompile identifier, no chain id). Any two unrelated TVM contracts that happen to hash the same `(address, permissionId, data)` tuple will accept the exact same signature, so a signature the account owner produced to authorize an action in Contract A can be replayed verbatim against Contract B.

### Finding Description
`ValidateMultiSign.execute()` builds the message hash as: [1](#0-0) 

```
byte[] address = words[0].toTronAddress();
int permissionId = words[1].intValueSafe();
byte[] data = words[2].getData();
byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
byte[] hash = Sha256Hash.hash(..., combine);
```

and then simply recovers each signer address from `hash` and checks weight against the account's `Permission`: [2](#0-1) 

Nothing in `combine` identifies *which contract* is asking for the check — not the calling contract's address, not a fixed constant unique to `ValidateMultiSign`, not the chain id. The only "scoping" values are `address` (the TRON account whose permission is checked) and `permissionId`, both of which are attacker/caller-supplied inputs to the precompile call, not something intrinsic to the verifying contract itself. This is structurally identical to the Futureswap flaw: the signed payload is "generic enough" that it carries no information tying it to a specific consuming application, so any contract that reconstructs the same `(address, permissionId, data)` tuple will accept it.

The precompile provides zero replay protection of its own (no nonce, no expiry) — any domain separation entirely depends on the calling contract choosing to embed contract-specific or nonce data inside `data`. If two independent TVM applications adopt similar conventions for `data` (e.g. both hash `(tokenAddress, amount, sequenceNumber)`), a signature the user produced and broadcast (e.g., visible on-chain or in a dApp's off-chain relay) for Application A's `validateMultiSign` call can be captured and replayed by anyone against Application B's `validateMultiSign` call for the same account/permissionId, causing Application B to treat an action as authorized by the account owner when the owner never intended to authorize anything in Application B.

### Impact Explanation
This does not let an attacker forge signatures or steal an account's private key, but it can cause unintended-authorization/griefing style impact against contracts built on top of `ValidateMultiSign`: an attacker who observes a validly-signed payload intended for one dApp can replay it to trigger the same threshold check in a second, unrelated dApp that happens to compute an identical hash, causing that second contract to execute privileged logic (approvals, withdrawals, state changes gated on `validateMultiSign` returning true) that the account owner did not intend for that contract. Because it is a "generic-message replay" issue rather than key compromise, the severity is bounded — similar to the original Futureswap report, which explicitly notes no theft of funds since actions still resolve to the legitimate account — but it can still cause funds/positions to move in an application the user never interacted with, or double-spend a single authorization across two integrations.

### Likelihood Explanation
Exploitability depends on downstream contracts using overlapping/naively-encoded `data` conventions (no embedded contract address, no nonce) when calling `ValidateMultiSign`, which is plausible for teams copying common patterns (much like multiple DeFi platforms independently adopting Futureswap's meta-tx pattern). Given `ValidateMultiSign` is a public, generically-documented TVM precompile intended to be reused across many contracts, and it offers no built-in domain separation, the likelihood of at least some deployed contracts being susceptible is non-trivial, though it requires two specific consuming contracts to coincide in their encoding.

### Recommendation
Bind the hash computed inside `ValidateMultiSign` (and `BatchValidateSign`) to caller-specific context that the signer cannot spoof — e.g., include the invoking contract's address (`CALLER`/message sender) and/or a fixed precompile-identifying constant and chain id in the `combine` buffer, so a signature computed for one calling contract cannot satisfy the check when replayed from a different calling contract. At minimum, document prominently that callers of `ValidateMultiSign` MUST include their own contract address and a monotonically increasing nonce in the `data` field, and consider adding an optional built-in nonce/expiry mechanism enforced by the precompile itself rather than leaving all replay protection to caller discipline.

### Proof of Concept
1. Deploy two TVM contracts, `ContractA` and `ContractB`, both calling the `validatemultisign(address,uint256,bytes32,bytes[])` precompile with `data = sha256(tokenAddress, amount, seq)` (a plausible convention neither embeds `msg.sender`/contract address).
2. Account owner signs `sha256(accountAddress || permissionId || data)` intending to authorize an action in `ContractA` (per the flow shown in `ValidateMultiSignContractTest.testDifferentCase`, which builds the hash exactly as `PrecompiledContracts.ValidateMultiSign` does): [3](#0-2) 
3. An observer captures this signature (from the `ContractA` transaction) and submits it as a call to `ContractB.someAction(tokenAddress, amount, seq, signatures)`, which forwards to the identical `validatemultisign(accountAddress, permissionId, data, signatures)` precompile call.
4. Because the precompile only checks `sha256(address || permissionId || data)` against the recovered signer weights — with no binding to `ContractA` vs `ContractB` — `ContractB`'s call returns the same "authorized" result, letting the replayed signature trigger `ContractB`'s privileged logic on behalf of the account owner without their intent.

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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L102-121)
```java
    //generate data

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
