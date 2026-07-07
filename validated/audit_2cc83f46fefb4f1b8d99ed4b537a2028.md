### Title
Missing `verifyingContract` Binding in Fast-Withdrawal Signature Hash Enables Cross-Deployment Replay — (`File: core/contracts/Verifier.sol`)

---

### Summary

`Verifier.requireValidTxSignatures()` hashes fast-withdrawal transactions as `keccak256(chainId ‖ idx ‖ txn)`. The WithdrawPool contract address is never committed to the hash. Any valid fast-withdrawal signature set obtained from one WithdrawPool deployment can be replayed verbatim against a second WithdrawPool deployment on the same chain, draining its liquidity.

---

### Finding Description

`BaseWithdrawPool.submitFastWithdrawal()` is a `public` function callable by any address. It delegates signature verification entirely to `Verifier.requireValidTxSignatures()`: [1](#0-0) 

Inside `requireValidTxSignatures`, the signed digest is constructed as:

```solidity
bytes32 data = keccak256(
    abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
);
``` [2](#0-1) 

The hash commits to `block.chainid`, the submission index `idx`, and the raw transaction bytes `txn`. It does **not** commit to the address of the WithdrawPool contract. This is the root cause.

Compare this with the order-digest path in `OffchainExchange.getDigest()`, which at least encodes a `verifyingContract` field (even if that field is `address(uint160(productId))` rather than `address(this)`): [3](#0-2) 

The fast-withdrawal path has no equivalent binding.

---

### Impact Explanation

When Nado upgrades or redeploys its WithdrawPool on the same chain (Ink Mainnet, chainId `57073`), the new contract starts with `minIdx = 0`: [4](#0-3) 

`submitFastWithdrawal` enforces only `idx > minIdx`: [5](#0-4) 

Any past fast-withdrawal call with `idx > 0` satisfies this check on the fresh deployment. Because the Schnorr multi-signatures produced by the verifier signers are public (they were broadcast on-chain in the original call), any user who previously received a fast withdrawal can re-submit the identical `(idx, txn, signatures)` tuple to the new WithdrawPool. The verifier's quorum check passes because the same signer set is reused across deployments, and the hash is identical (same chainId, same idx, same txn bytes). The new pool then transfers tokens to the original `sendTo` address a second time, draining its liquidity.

Corrupted state: `WithdrawPool.fees`, token balances of the pool, and the `markedIdxs` bitmap (which is fresh on the new deployment and therefore does not block the replay).

---

### Likelihood Explanation

- **Trigger**: Any user who holds a previously broadcast `(idx, txn, signatures)` tuple — all of which are on-chain calldata — can call `submitFastWithdrawal` on the new deployment with no special privilege.
- **Precondition**: A second WithdrawPool deployment on the same chain. This is a normal operational event (upgrades, migrations). The protocol's `ContractOwner.setWithdrawPool()` confirms this is an expected admin action. [6](#0-5) 

- **No sequencer or owner compromise required.** The attacker is the original withdrawal recipient, an unprivileged user.

---

### Recommendation

Bind the WithdrawPool contract address into the signed digest inside `requireValidTxSignatures`:

```solidity
bytes32 data = keccak256(
    abi.encodePacked(uint256(block.chainid), address(this), uint256(idx), txn)
);
```

This mirrors the EIP-712 `verifyingContract` field and ensures signatures are non-transferable across deployments. Existing in-flight signatures must be invalidated or a version bump introduced when deploying a new pool.

---

### Proof of Concept

1. User calls `submitFastWithdrawal(100, txn, sigs)` on WithdrawPool **A** (Ink Mainnet, chainId `57073`). Funds are transferred. The call is recorded in on-chain calldata.
2. Protocol upgrades: `ContractOwner.setWithdrawPool(addrB)` points to a fresh WithdrawPool **B** with `minIdx = 0` and empty `markedIdxs`.
3. User (or any observer) replays the identical call: `submitFastWithdrawal(100, txn, sigs)` on WithdrawPool **B**.
4. `require(100 > 0)` passes. `markedIdxs[100]` is `false`. `requireValidTxSignatures` recomputes `keccak256(57073, 100, txn)` — identical to the original — and the quorum check passes against the same signer set.
5. `handleWithdrawTransfer` sends tokens from **B** to `sendTo` a second time. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L42-43)
```text
    uint64 public minIdx;

```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/Verifier.sol (L261-289)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
        bytes32 hashedMsg = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", data)
        );

        uint256 nSignatures = 0;
        for (uint256 i = 0; i < signatures.length; i++) {
            if (signatures[i].length > 0) {
                nSignatures += 1;
                require(
                    checkIndividualSignature(
                        hashedMsg,
                        signatures[i],
                        uint8(i)
                    ),
                    "invalid signature"
                );
            }
        }
        require(nSignatures == nSigner, "not enough signatures");
    }
```

**File:** core/contracts/OffchainExchange.sol (L311-321)
```text
        bytes32 domainSeparator = keccak256(
            abi.encode(
                _TYPE_HASH,
                _EIP712NameHash(),
                _EIP712VersionHash(),
                block.chainid,
                address(uint160(productId))
            )
        );

        return ECDSAUpgradeable.toTypedDataHash(domainSeparator, structHash);
```

**File:** core/contracts/ContractOwner.sol (L467-469)
```text
    function setWithdrawPool(address _withdrawPool) external onlyOwner {
        clearinghouse.setWithdrawPool(_withdrawPool);
    }
```
