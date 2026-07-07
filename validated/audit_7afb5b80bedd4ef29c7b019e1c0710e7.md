### Title
No Token Recovery Mechanism Causes Permanent Lock of Unclaimed Airdrop Tokens — (`core/contracts/Airdrop.sol`)

---

### Summary

`Airdrop.sol` accepts ERC20 tokens for weekly distribution but exposes no function to recover tokens that are never claimed. Any tokens loaded into the contract that are not claimed — including allocations for sanctioned addresses who are explicitly blocked from claiming — are permanently locked with no on-chain recovery path.

---

### Finding Description

The `Airdrop` contract is funded with ERC20 tokens (via direct transfer to the contract address) and distributes them weekly to eligible users via Merkle-proof-gated `claim()` calls.

The only token outflow path in the entire contract is:

```solidity
SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
```

inside `_claim()`. [1](#0-0) 

This path is gated by `_verifyProof`, which explicitly **blocks sanctioned addresses** from claiming:

```solidity
require(
    !ISanctionsList(sanctions).isSanctioned(sender),
    "address is sanctioned."
);
``` [2](#0-1) 

The contract's full function surface is:

| Function | Access | Token movement |
|---|---|---|
| `initialize` | `initializer` | none |
| `registerMerkleRoot` | `onlyOwner` | none |
| `claim` | public | outflow only |
| `getClaimed` | view | none | [3](#0-2) 

There is no `withdraw`, `recoverTokens`, `sweep`, or any other function that moves tokens out of the contract except through a valid, unsanctioned claim. The owner can register Merkle roots but cannot touch the token balance.

This creates two concrete permanent-lock scenarios:

1. **Sanctioned-address allocations**: If a user included in a Merkle tree is later added to the sanctions list, their allocation can never be claimed. The tokens sit in the contract forever.
2. **Unclaimed allocations**: Users who never submit a claim (lost keys, inactivity, etc.) leave their allocation permanently locked. The `claimed` mapping records `0` for them indefinitely, but no sweep mechanism exists. [4](#0-3) 

---

### Impact Explanation

ERC20 tokens loaded into `Airdrop.sol` that are not claimed are permanently irrecoverable. The magnitude scales with the total unclaimed allocation across all weeks. Because `pastWeeks` grows monotonically and each week's Merkle root is immutable once set, there is no administrative path to reclaim any portion of the locked balance. [5](#0-4) 

---

### Likelihood Explanation

Moderate-to-high. The sanctions check is an active, enforced gate — any address sanctioned after a Merkle root is registered but before claiming will have its allocation permanently locked. Additionally, partial non-claim rates are normal in any airdrop program. Both scenarios are realistic and require no attacker action; they arise from ordinary protocol operation. [2](#0-1) 

---

### Recommendation

Add an owner-controlled token recovery function to allow retrieval of unclaimed or excess tokens after a suitable delay:

```solidity
function recoverTokens(address to, uint256 amount) external onlyOwner {
    SafeERC20.safeTransfer(IERC20(token), to, amount);
}
```

Optionally, enforce a time-lock (e.g., only callable after `N` weeks past the last registered week) to prevent premature sweeping of legitimately claimable tokens.

---

### Proof of Concept

1. Owner calls `registerMerkleRoot(1, root)` where `root` commits to allocations including address `A` for `1000e18` tokens.
2. Owner transfers `1000e18` tokens to the `Airdrop` contract.
3. Address `A` is added to the OFAC sanctions list before claiming.
4. `A` calls `claim(...)` → `_verifyProof` reverts: `"address is sanctioned."` [2](#0-1) 
5. No other address can claim `A`'s allocation (Merkle leaf is keyed to `A`'s address). [6](#0-5) 
6. Owner has no function to recover the `1000e18` tokens. They are permanently locked in the contract. [3](#0-2)

### Citations

**File:** core/contracts/Airdrop.sol (L11-96)
```text
contract Airdrop is OwnableUpgradeable, IAirdrop {
    address internal token;
    address internal sanctions;
    uint32 internal pastWeeks;

    mapping(uint32 => bytes32) internal merkleRoots;
    mapping(uint32 => mapping(address => uint256)) internal claimed;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(address _token, address _sanctions)
        external
        initializer
    {
        __Ownable_init();
        token = _token;
        sanctions = _sanctions;
    }

    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }

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

    function getClaimed(address account)
        external
        view
        returns (uint256[] memory)
    {
        uint256[] memory result = new uint256[](pastWeeks + 1);
        for (uint32 week = 1; week <= pastWeeks; week++) {
            result[week] = claimed[week][account];
        }
        return result;
    }
}
```
