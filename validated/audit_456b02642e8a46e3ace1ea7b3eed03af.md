### Title
Sanctioned User's Airdrop Allocation Permanently Locked With No Admin Recovery Path - (File: `core/contracts/Airdrop.sol`)

### Summary
The `Airdrop` contract enforces a sanctions check inside `_verifyProof()` as the sole gate for token distribution. If a user is included in a registered Merkle root but becomes sanctioned before claiming, their allocated tokens are permanently locked in the contract. The contract owner has no function to recover, redirect, or otherwise move those tokens.

### Finding Description
When a user calls `claim()`, the call chain is:

`claim()` → `_claim()` → `_verifyProof()`

Inside `_verifyProof()`, the sanctions check is enforced unconditionally:

```solidity
require(
    !ISanctionsList(sanctions).isSanctioned(sender),
    "address is sanctioned."
);
``` [1](#0-0) 

Because the check reverts before `claimed[week][sender] = totalAmount` is ever written, the user's allocation is never marked as claimed and the tokens remain in the contract indefinitely. [2](#0-1) 

The entire set of owner-callable functions in `Airdrop` is limited to `registerMerkleRoot`. There is no `recoverTokens`, `adminClaim`, or any other function that would allow the owner to move tokens out of the contract on behalf of a sanctioned user or to reclaim unclaimable allocations. [3](#0-2) 

The Merkle root is immutable once registered — there is no mechanism to remove a specific leaf or replace a recipient address after the fact.

### Impact Explanation
Any airdrop tokens allocated to an address that is sanctioned at claim time are permanently locked in the `Airdrop` contract. The owner cannot recover them, redirect them to a different address, or burn them. The total locked amount equals the `totalAmount` encoded in the Merkle leaf for every sanctioned claimant across all registered weeks. This is a direct, irreversible asset loss from the protocol's perspective.

### Likelihood Explanation
OFAC and other sanctions lists are dynamic. A user can be added to a sanctions list at any time after a Merkle root is registered. Given that airdrop campaigns typically span weeks and the `pastWeeks` counter increments with each new root, there is a realistic window during which a previously eligible user becomes sanctioned. The `Airdrop` contract is deployed on a public chain and the `claim()` function is callable by anyone, making the sanction check the only barrier — no off-chain coordination can bypass it.

### Recommendation
Add an owner-only recovery function that allows the protocol to reclaim tokens allocated to sanctioned or otherwise unclaimable addresses. One approach is to mark the allocation as consumed and transfer the tokens to a designated treasury address:

```solidity
function recoverSanctionedAllocation(
    uint32 week,
    address sanctionedUser,
    uint256 totalAmount,
    bytes32[] calldata proof,
    address treasury
) external onlyOwner {
    require(claimed[week][sanctionedUser] == 0, "Already claimed.");
    require(ISanctionsList(sanctions).isSanctioned(sanctionedUser), "Not sanctioned.");
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(sanctionedUser, totalAmount))));
    require(MerkleProof.verify(proof, merkleRoots[week], leaf), "Invalid proof.");
    claimed[week][sanctionedUser] = totalAmount;
    SafeERC20.safeTransfer(IERC20(token), treasury, totalAmount);
}
```

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, root)` where `root` encodes `(alice, 1000e18)`.
2. Alice is added to the OFAC sanctions list before she calls `claim()`.
3. Alice (or anyone on her behalf) calls `claim([{week: 1, totalAmount: 1000e18, proof: [...]}])`.
4. Execution reaches `_verifyProof` → `require(!ISanctionsList(sanctions).isSanctioned(alice))` → **reverts**.
5. `claimed[1][alice]` remains `0`; 1000e18 tokens remain in the `Airdrop` contract.
6. Owner inspects the contract: `registerMerkleRoot` is the only callable function. There is no path to move the 1000e18 tokens. They are permanently locked. [4](#0-3)

### Citations

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

**File:** core/contracts/Airdrop.sol (L42-83)
```text
    function _verifyProof(
        uint32 week,
        address sender,
        uint256 totalAmount,
        bytes32[] calldata proof
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
