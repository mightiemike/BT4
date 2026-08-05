### Title
`ValidateMultiSign`/`BatchValidateSign` TVM precompiles omit a calling-contract/domain identifier from the signed hash, enabling cross-contract signature replay - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
TRON's `ValidateMultiSign` (address `0x0a`) and `BatchValidateSign` (address `0x09`) precompiles are the on-chain building block that TVM smart contracts (marketplaces, escrow/bid systems, bridges, etc.) use to verify that an off-chain user signature authorizes an action, exactly analogous to ParaSpace's `verifyCreditSignature`/`Credit` struct used to authorize a maker's bid. Just like the ParaSpace `Credit` struct lacked a `MarketplaceAddress` field binding the signature to a specific marketplace/order context, these precompiles hash only `(account address, permissionId, caller-supplied data)` — they never mix in the identity of the calling contract itself. Any TRON smart contract that asks a user to sign a message and checks it via these precompiles, without independently embedding its own contract address (or another domain identifier) inside the `data`/`hash` it feeds to the precompile, is exposed to the same class of bug: a signature obtained for use in "marketplace/contract A" can be replayed to authorize an unrelated, attacker-chosen action in "marketplace/contract B" whenever the encoded payload happens to coincide.

### Finding Description
`ValidateMultiSign.execute` builds the hash to be checked as: [1](#0-0) 

```
byte[] address = words[0].toTronAddress();
int permissionId = words[1].intValueSafe();
byte[] data = words[2].getData();

byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
byte[] hash = Sha256Hash.hash(..., combine);
```

`BatchValidateSign.doExecute` is even more permissive — the caller supplies the raw `hash` directly, with no binding at all: [2](#0-1) 

In both cases, weight/threshold checking against the account's `Permission` is done purely against whatever `hash`/`data` the *calling contract* passes in: [3](#0-2) 

There is no mandatory inclusion of the invoking contract's address, a chain/domain identifier, or any other "verifying context" tag comparable to ParaSpace's missing `MarketplaceAddress`. This mirrors the root cause of the referenced report precisely: `Credit.orderId`/`token`/`amount` were signed without a marketplace identifier, so a signature meant for one marketplace/order could be reused against another marketplace's cheaper order. Here, an account owner's signature meant to authorize "action X in contract A" (e.g., "release NFT #1 to buyer" in Marketplace A) can be replayed unchanged against contract B if B happens to compute the same `(address, permissionId, data)` triple (or the same raw `hash` for `BatchValidateSign`) for a different, attacker-favorable action (e.g., "release worthless NFT #999" or "approve withdrawal to attacker").

This is a documented but easily-missed pitfall of the precompile's interface: unlike well-designed EIP-712-style domain separation (which binds signatures to `verifyingContract` + `chainId`), TRON's `ValidateMultiSign`/`BatchValidateSign` push the entire responsibility for domain separation onto each dApp author, and provide no protection or even guidance encoded at the protocol level. Any contract author who (reasonably, by analogy to `ecrecover`) assumes the precompile is safe to use directly on business data (like an order id or amount) without prepending their own contract address inherits exactly the ParaSpace-class vulnerability.

### Impact Explanation
An unprivileged attacker who operates or interacts with two independent TVM contracts that both consume `ValidateMultiSign`/`BatchValidateSign` for authorization (e.g., two NFT/asset marketplaces, or a marketplace and an escrow/lending contract) can harvest a victim's signature intended for one context and replay it to unlock value in a different context — the account's signed authorization is not bound to the contract or purpose it was created for. As with the original ParaSpace bug, the severity scales with what the "action" actually does (e.g., authorizing release of high-value assets, approving credit/loan draws, or unlocking escrowed funds), and can reach full loss of the signed account's earmarked assets to an attacker-chosen counterparty.

### Likelihood Explanation
Exploitability requires only that a victim sign a message intended for a specific contract/purpose and that another (attacker-controlled or attacker-observed) contract accept the identical `(address, permissionId, data)`/`hash` combination for a different action — no privileged role, insider access, or protocol change is required. This is a realistic outcome for the TRON dApp ecosystem because many independent, unaffiliated contracts share this exact same precompile-level primitive, and its documentation/design does not enforce any per-contract domain separation, making it likely that at least some deployed contracts (especially copy-pasted marketplace/bid-style contracts) omit their own contract address from the signed payload — the exact same "assume the primitive is self-contained" mistake ParaSpace made with its `Credit` struct.

### Recommendation
Harden the precompile-level contract so that domain separation is enforced by the protocol rather than left to each TVM contract author:
- Extend `ValidateMultiSign`/`BatchValidateSign` (or add a new, opt-in variant) so the hash mandatorily incorporates the calling contract's address (`this`/`CALLER`) and, ideally, a chain identifier, e.g. `hash = SHA256(callingContractAddress || chainId || address || permissionId || data)`.
- At minimum, update the TIP/documentation for these precompiles to explicitly require dApp developers to prepend their own contract address (and a purpose/domain tag) into `data` before hashing, and provide reference implementations that do so, to reduce the chance of ecosystem-wide repeats of the ParaSpace-class bug.

### Proof of Concept
1. Contract A (e.g., "Marketplace A") asks user U to sign `data_A` (an encoding of `orderId_A`, price, etc.) and later calls `ValidateMultiSign(U, permissionId, data_A, [sig])` to authorize release of a high-value NFT to the taker.
2. Contract B (e.g., "Marketplace B", or a copy-pasted clone) is built by the same or a different developer and, coincidentally or maliciously, encodes its own `orderId_B` payload identically to `data_A` (same bytes) — for example both use a raw `bytes32 orderId` with no additional context, and the attacker crafts `orderId_B` to collide with `data_A`.
3. Attacker observes U's signature `sig` from a public marketplace-A transaction (all signatures are inherently public on-chain) and submits it to Marketplace B's function that calls `ValidateMultiSign(U, permissionId, data_B, [sig])`.
4. Because the precompile hash only depends on `(U, permissionId, data)` and `data_B == data_A`, `PrecompiledContracts.ValidateMultiSign.execute` at lines [4](#0-3)  returns success, and Marketplace B treats U's signature as valid authorization for the attacker-chosen action in Marketplace B (e.g., transferring U's assets or approving a credit-based purchase of a worthless item), exactly mirroring the ParaSpace `matchBidWithTakerAsk` cross-marketplace replay.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1057-1065)
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1162-1163)
```java
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();
```
