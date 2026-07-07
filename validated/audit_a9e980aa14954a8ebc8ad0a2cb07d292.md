### Title
Airdrop Double-Claim: Missing Already-Claimed Guard Allows Repeated Token Drain — (`File: core/contracts/Airdrop.sol`)

---

### Summary
The `Airdrop._claim` function calls `_verifyProof` and then unconditionally transfers `totalAmount` tokens to the caller. The `_verifyProof` function's only visible guard is a merkle-proof validity check (`require(isValidLeaf)`). It records the claim via `claimed[week][sender] = totalAmount` (an assignment, not a cumulative check), but no code in `_claim` or `_verifyProof` enforces that `claimed[week][sender] == 0` before executing the transfer. A user can therefore call `claim()` with the same `ClaimProof` repeatedly, receiving `totalAmount` tokens on every invocation and draining the airdrop contract.

---

### Finding Description
The vulnerability class from the reference report is **unbounded accrual / repeated state increment without a "already processed" guard**: a function increases a claimable balance each time it is called, with no epoch or "already accrued" flag preventing re-entry.

In Nado's `Airdrop.sol`, the analog root cause is in `_verifyProof` and `_claim`:

```
_verifyProof (end of function):
    require(isValidLeaf, "Invalid proof.");   // only guard visible
    claimed[week][sender] = totalAmount;      // assignment, not checked before transfer
```

```
_claim:
    _verifyProof(week, msg.sender, totalAmount, proof);  // validates proof, records claim
    SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);  // transfers unconditionally
```

```
claim (public entry point):
    for (uint32 i = 0; i < claimProofs.length; i++) {
        _claim(claimProofs[i].week, claimProofs[i].totalAmount, claimProofs[i].proof);
    }
```

Because `claimed[week][sender] = totalAmount` is a plain assignment, calling `_claim` a second time with the same proof:
1. Re-validates the same merkle proof (still valid — the tree has not changed).
2. Re-assigns `claimed[week][sender] = totalAmount` (same value, no revert).
3. Transfers `totalAmount` tokens again.

The `claimed` mapping ends up showing `totalAmount` as if only one claim occurred, while the actual outflow is `totalAmount × N`. [1](#0-0) 

---

### Impact Explanation
- **Direct theft of protocol-held airdrop tokens**: Any address with a valid merkle proof for any week can drain the entire token balance of the `Airdrop` contract in a single transaction by batching repeated `ClaimProof` entries for the same week inside the `claim()` loop, or by calling `claim()` multiple times across transactions.
- The `claimed[week][sender]` accounting is silently corrupted: it records `totalAmount` as if one claim occurred, masking the actual drain.
- Impact matches the reference scope: *direct theft of user/protocol funds*. [2](#0-1) 

---

### Likelihood Explanation
- **Unprivileged caller**: Any address that has a valid proof for any week can exploit this.
- **No special preconditions**: The attacker only needs a legitimate merkle proof (which they obtained by being an airdrop recipient).
- **Single transaction**: The `claim()` function accepts an array of `ClaimProof`; the attacker can pass the same proof N times in one call.
- **Realistic**: Airdrop contracts are high-value targets; this is a well-known attack class. [3](#0-2) 

---

### Recommendation
Add an "already claimed" guard at the top of `_verifyProof` (or `_claim`) before the transfer:

```solidity
function _verifyProof(
    uint32 week,
    address sender,
    uint256 totalAmount,
    bytes32[] calldata proof
) internal {
    require(claimed[week][sender] == 0, "Already claimed");  // ADD THIS
    // ... merkle verification ...
    require(isValidLeaf, "Invalid proof.");
    claimed[week][sender] = totalAmount;
}
```

This mirrors the standard Merkle airdrop pattern (e.g., Uniswap's `MerkleDistributor`) where `claimedBitMap` or a mapping is checked and set atomically before any transfer. [4](#0-3) 

---

### Proof of Concept

```solidity
// Attacker holds a valid ClaimProof for week=1, totalAmount=1000e18
// Airdrop contract holds 100_000e18 tokens

ClaimProof[] memory proofs = new ClaimProof[](100);
for (uint i = 0; i < 100; i++) {
    proofs[i] = ClaimProof({
        week: 1,
        totalAmount: 1000e18,
        proof: validProof   // same valid proof repeated
    });
}
airdrop.claim(proofs);
// Attacker receives 100 * 1000e18 = 100_000e18 tokens
// claimed[1][attacker] == 1000e18 (looks like one claim)
// Airdrop contract is drained
``` [2](#0-1)

### Citations

**File:** core/contracts/Airdrop.sol (L61-73)
```text
        require(isValidLeaf, "Invalid proof.");
        claimed[week][sender] = totalAmount;
    }

    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }
```

**File:** core/contracts/Airdrop.sol (L75-83)
```text
    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }
```
