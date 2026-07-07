I have all the code needed to evaluate this. Let me analyze the exact leaf construction and guard logic.

### Title
Cross-Chain Merkle Proof Replay Enables Double-Spend of Airdrop Tokens — (`core/contracts/Airdrop.sol`)

---

### Summary

The Merkle leaf in `_verifyProof` encodes only `(sender, totalAmount)` with no `chainId` and no contract address. If the same Merkle root is registered on two chains, a user can submit an identical `ClaimProof` on both chains and receive `totalAmount` tokens on each, doubling their allocation.

---

### Finding Description

In `_verifyProof`, the leaf is constructed as:

```solidity
bytes32 leaf = keccak256(
    bytes.concat(keccak256(abi.encode(sender, totalAmount)))
);
``` [1](#0-0) 

The leaf binds only to `sender` and `totalAmount`. It does not commit to:
- `block.chainid`
- `address(this)` (the contract address)
- `week`

The replay guard is the chain-local `claimed` mapping:

```solidity
mapping(uint32 => mapping(address => uint256)) internal claimed;
``` [2](#0-1) 

The check `require(claimed[week][sender] == 0, "Already claimed.")` [3](#0-2)  only reads from the current chain's storage. On a second chain, `claimed[week][sender]` is independently `0`, so the guard passes unconditionally.

The Merkle root is registered per-chain by the owner via `registerMerkleRoot`. [4](#0-3)  When the same allocation snapshot is used across chains (standard multi-chain deployment practice), the same `merkleRoot` is written to both chains, making the same proof valid on both.

---

### Impact Explanation

A user allocated `totalAmount` tokens can claim on every chain where the same Merkle root is registered, receiving `N × totalAmount` tokens total across `N` chains. The protocol's airdrop treasury on each chain is drained by the full `totalAmount` per user per chain. This is a direct, unbounded asset loss proportional to the number of chains deployed.

---

### Likelihood Explanation

Multi-chain deployment of the same protocol with the same weekly allocation snapshot is the standard operational pattern for DeFi protocols. The owner registering the same `merkleRoot` on two chains is not a mistake — it is the expected behavior. No attacker capability beyond holding a valid allocation is required. Any allocated user can execute this without any privileged access.

---

### Recommendation

Bind the leaf to the chain and contract at construction time. Replace the leaf encoding with:

```solidity
bytes32 leaf = keccak256(
    bytes.concat(
        keccak256(abi.encode(block.chainid, address(this), week, sender, totalAmount))
    )
);
```

This ensures a proof generated for Chain A is cryptographically invalid on Chain B, and also binds the proof to the specific week and contract instance. The Merkle tree must be regenerated off-chain to include these fields in each leaf.

---

### Proof of Concept

```solidity
// Hardhat fork test (pseudocode)
// 1. Deploy Airdrop on fork of Chain A (chainId=1) and Chain B (chainId=42161)
//    with identical state: same merkleRoot registered for week=1
// 2. On Chain A fork:
airdropA.claim([{week: 1, totalAmount: 1000e18, proof: validProof}]);
// assert: tokenA.balanceOf(attacker) == 1000e18
// assert: airdropA.getClaimed(attacker)[1] == 1000e18

// 3. On Chain B fork (claimed mapping is 0 — independent state):
airdropB.claim([{week: 1, totalAmount: 1000e18, proof: validProof}]); // same proof
// assert: tokenB.balanceOf(attacker) == 1000e18
// assert: airdropB.getClaimed(attacker)[1] == 1000e18

// Total received: 2000e18 against a single 1000e18 allocation
```

The `_verifyProof` call on Chain B passes because: (a) `claimed[1][attacker] == 0` on Chain B, (b) `merkleRoots[1]` equals the same root, and (c) the leaf `keccak256(bytes.concat(keccak256(abi.encode(attacker, 1000e18))))` is identical on both chains. [5](#0-4)

### Citations

**File:** core/contracts/Airdrop.sol (L17-17)
```text
    mapping(uint32 => mapping(address => uint256)) internal claimed;
```

**File:** core/contracts/Airdrop.sol (L33-40)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }
```

**File:** core/contracts/Airdrop.sol (L47-62)
```text
    ) internal {
        require(claimed[week][sender] == 0, "Already claimed.");
        require(
            merkleRoots[week] != bytes32(0),
            "Week hasn't been registered."
        );
        require(
            !ISanctionsList(sanctions).isSanctioned(sender),
            "address is sanctioned."
        );
        bytes32 leaf = keccak256(
            bytes.concat(keccak256(abi.encode(sender, totalAmount)))
        );
        bool isValidLeaf = MerkleProof.verify(proof, merkleRoots[week], leaf);
        require(isValidLeaf, "Invalid proof.");
        claimed[week][sender] = totalAmount;
```
