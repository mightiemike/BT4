### Title
DirectDepositV1 Cannot Claim Airdrop Rewards, Causing Permanent Token Lock - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

The `DirectDepositV1` contract acts as a smart-contract deposit address that calls `depositCollateralWithReferral` on the `Endpoint` on behalf of a user's subaccount. Because `DirectDepositV1` is the on-chain `msg.sender` for all deposits it processes, the protocol's off-chain Merkle tree builder may attribute airdrop rewards to its contract address. However, `DirectDepositV1` contains no function to call `claim()` on the `Airdrop` contract, making any rewards attributed to its address permanently unclaimable and locked inside `Airdrop`.

---

### Finding Description

The `Airdrop` contract distributes protocol tokens weekly via Merkle proofs. Its `_claim` function hardcodes `msg.sender` as both the verified leaf address and the transfer recipient:

```solidity
// Airdrop.sol L70-72
_verifyProof(week, msg.sender, totalAmount, proof);
SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
```

This means only the exact address encoded in the Merkle leaf can claim its allocation — no proxy or delegated claim is possible.

`DirectDepositV1` is a smart contract deployed per user. Its `creditDeposit()` function iterates over all spot products and calls `endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1")` — making `DirectDepositV1`'s own contract address the `msg.sender` visible to the `Endpoint` and, by extension, the address that the off-chain reward indexer would associate with deposit activity:

```solidity
// DirectDepositV1.sol L83-100
function creditDeposit() external {
    ...
    endpoint.depositCollateralWithReferral(
        subaccount, productId, uint128(balance), "-1"
    );
    ...
}
```

`DirectDepositV1` exposes only two asset-recovery paths:

| Function | What it recovers |
|---|---|
| `withdraw(token)` | ERC20 tokens **already held** by `DirectDepositV1` |
| `withdrawNative()` | Native ETH **already held** by `DirectDepositV1` |

Neither path can reach tokens sitting inside the `Airdrop` contract. There is no function in `DirectDepositV1` that calls `IAirdrop.claim()`. As a result, any airdrop allocation attributed to a `DirectDepositV1` address is permanently locked in `Airdrop`.

---

### Impact Explanation

Airdrop tokens allocated to `DirectDepositV1` contract addresses are permanently locked in the `Airdrop` contract. The owner cannot recover them via `withdraw()` (which only drains the `DirectDepositV1` balance), and no other recovery path exists. The magnitude scales with the number of deployed `DirectDepositV1` instances and the weekly reward amounts attributed to them.

---

### Likelihood Explanation

Medium. `DirectDepositV1` is the on-chain depositor address visible to the `Endpoint`. Off-chain reward indexers that attribute rewards based on deposit volume or trading activity would naturally associate rewards with the depositing address — which is `DirectDepositV1`, not the user's EOA. If the Merkle tree is built from `Endpoint` event data (e.g., `depositCollateralWithReferral` calls), `DirectDepositV1` addresses will appear as participants. The protocol documentation confirms the Airdrop is a weekly participant-reward system, making this scenario realistic.

---

### Recommendation

Add a function to `DirectDepositV1` that allows the owner to call `claim()` on the `Airdrop` contract and forward the received tokens to the owner or directly re-deposit them:

```solidity
function claimAirdrop(address airdrop, IAirdrop.ClaimProof[] calldata proofs) external onlyOwner {
    IAirdrop(airdrop).claim(proofs);
    // optionally: forward claimed tokens to owner or re-deposit
}
```

This mirrors the pattern recommended in the external report: add an explicit interaction function so the contract can retrieve rewards it is entitled to, which can then be forwarded via the existing `withdraw()` path.

---

### Proof of Concept

1. User deploys `DirectDepositV1` pointing to their subaccount.
2. User sends USDC to `DirectDepositV1`; anyone calls `creditDeposit()`, which calls `endpoint.depositCollateralWithReferral(subaccount, ...)` — `DirectDepositV1`'s address is the on-chain depositor.
3. Protocol's off-chain indexer builds the weekly Merkle tree; `DirectDepositV1`'s address is included as a reward recipient with allocation `X`.
4. Owner calls `withdraw(usdc)` on `DirectDepositV1` — this only drains tokens held by `DirectDepositV1` itself; the `X` tokens in `Airdrop` are untouched.
5. No function in `DirectDepositV1` can call `Airdrop.claim()`.
6. `Airdrop._verifyProof` at [1](#0-0)  encodes the leaf as `keccak256(abi.encode(DirectDepositV1_address, X))` — only `DirectDepositV1` itself can satisfy `msg.sender == DirectDepositV1_address`, but it has no code path to do so.
7. The `X` tokens remain permanently locked in `Airdrop`.

**Root cause**: [2](#0-1)  — `withdraw()` only recovers tokens already in `DirectDepositV1`; there is no corresponding function to interact with the `Airdrop` contract.

**Missing interaction point**: [3](#0-2)  — `_claim` unconditionally sends to `msg.sender`, so only the exact leaf address can ever receive its allocation.

**Depositor identity established here**: [4](#0-3)  — `creditDeposit()` makes `DirectDepositV1` the on-chain depositor address.

### Citations

**File:** core/contracts/Airdrop.sol (L57-62)
```text
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

**File:** core/contracts/DirectDepositV1.sol (L83-100)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
