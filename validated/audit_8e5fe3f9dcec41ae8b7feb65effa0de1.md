### Title
Airdrop `claim` Hardcodes `msg.sender` as Beneficiary, Permanently Locking Tokens for Smart-Contract Wallet and Vesting-Contract Participants — (`File: core/contracts/Airdrop.sol`)

---

### Summary

`Airdrop._claim` uses `msg.sender` as both the Merkle-proof identity and the ERC-20 transfer recipient. There is no mechanism to specify a different beneficiary. Any participant whose address in the Merkle tree is a smart contract with restricted outbound call capabilities (e.g., a vesting contract that whitelists permitted function signatures) cannot call `claim`, and their allocation is permanently locked in the contract.

---

### Finding Description

`Airdrop._claim` is the sole internal path for all airdrop redemptions:

```solidity
// core/contracts/Airdrop.sol  lines 65-73
function _claim(
    uint32 week,
    uint256 totalAmount,
    bytes32[] calldata proof
) internal {
    _verifyProof(week, msg.sender, totalAmount, proof);          // identity = msg.sender
    SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount); // recipient = msg.sender
    emit Claim(msg.sender, week, totalAmount);
}
``` [1](#0-0) 

The Merkle leaf is constructed inside `_verifyProof` using the same `sender` argument:

```solidity
// core/contracts/Airdrop.sol  lines 57-61
bytes32 leaf = keccak256(
    bytes.concat(keccak256(abi.encode(sender, totalAmount)))
);
bool isValidLeaf = MerkleProof.verify(proof, merkleRoots[week], leaf);
require(isValidLeaf, "Invalid proof.");
``` [2](#0-1) 

The public `IAirdrop` interface exposes only `claim(ClaimProof[])` — there is no `claimFor(address beneficiary, …)` overload and no beneficiary parameter anywhere in the interface: [3](#0-2) 

Because both the proof check and the token transfer are pinned to `msg.sender`, the only address that can successfully redeem a given leaf is the exact address encoded in the Merkle tree. If that address is a smart contract whose outbound call set is restricted (a vesting contract, a multisig with a function-signature allowlist, etc.), the contract cannot call `claim` and the allocation is irrecoverable.

---

### Impact Explanation

Participants whose Merkle-tree address is a restricted smart contract permanently lose their entire airdrop allocation for every affected week. `SafeERC20.safeTransfer` will never be reached for those leaves; the tokens remain locked in the `Airdrop` contract with no admin-rescue path visible in the contract. The corrupted state is: `claimed[week][address]` stays `0` forever while the corresponding token balance sits idle in the contract.

---

### Likelihood Explanation

Medium. Vesting contracts and smart-contract wallets (Gnosis Safe, etc.) are standard infrastructure in DeFi. Any protocol participant onboarded through such a contract whose contract address — rather than an EOA — was included in the Merkle snapshot is affected. The protocol cannot retroactively fix already-published Merkle roots without redeploying the contract or adding an owner-only rescue function.

---

### Recommendation

Add a `claimFor(address beneficiary, ClaimProof[] calldata claimProofs)` entry point that passes `beneficiary` (instead of `msg.sender`) to `_verifyProof` and `safeTransfer`. To prevent griefing (front-running a claim to a different address), require either that `msg.sender == beneficiary` or that `beneficiary` has pre-authorized `msg.sender` via a signed permit or an on-chain allowance mapping. This mirrors the fix applied in the referenced TAP-contracts PR #58, where the account identity was derived from an authoritative on-chain source rather than from the raw caller.

---

### Proof of Concept

1. The Nado team publishes a Merkle root for week `W`. One leaf encodes `(vestingContract, 1000e18)` — a vesting contract address that whitelists only specific function signatures.
2. `vestingContract` attempts to call `Airdrop.claim([{week: W, totalAmount: 1000e18, proof: [...]}])`.
3. Inside `_claim`, `_verifyProof(W, msg.sender /*= vestingContract*/, 1000e18, proof)` is called. The leaf recomputed on-chain matches the Merkle tree, so the proof passes and `claimed[W][vestingContract] = 1000e18` is written.
4. `SafeERC20.safeTransfer(token, vestingContract, 1000e18)` executes — tokens arrive at `vestingContract`.

Wait — step 4 succeeds if the vesting contract *can* call `claim`. The blocking scenario is the inverse: if `vestingContract`'s allowlist does **not** include the `claim` selector, the call never reaches step 3. The vesting contract cannot initiate the transaction at all, `claimed[W][vestingContract]` stays `0`, and the 1000e18 tokens are permanently stranded in `Airdrop`. No alternative entry point exists in `IAirdrop` to recover them. [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/Airdrop.sol (L42-62)
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
```

**File:** core/contracts/Airdrop.sol (L65-73)
```text
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

**File:** core/contracts/interfaces/IAirdrop.sol (L13-13)
```text
    function claim(ClaimProof[] calldata claimProofs) external;
```
