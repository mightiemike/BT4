### Title
`Verifier.requireValidTxSignatures` Passes Trivially When `nSigner == 0`, Enabling Unauthorized `submitFastWithdrawal` Drain — (`core/contracts/Verifier.sol`, `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` enforces `require(nSignatures == nSigner)`. Because `nSigner` is a storage variable that defaults to `0` and is only incremented when non-zero pubkeys are assigned, the Verifier is "active" (callable and accepting) even when no signers are configured. With `nSigner == 0`, an empty `signatures[]` array satisfies the check trivially (`0 == 0`), bypassing all signature validation. Any unprivileged caller can exploit this to drain the `WithdrawPool` via `submitFastWithdrawal`.

---

### Finding Description

`Verifier.nSigner` is a plain storage variable initialized to `0` by default. The `initialize` function only increments it for non-zero pubkeys: [1](#0-0) 

If all 8 entries in `initialSet` are `Point(0,0)` (the "none" sentinel), `nSigner` remains `0` after initialization. The same state is reachable during key rotation via `deletePubkey`, which decrements `nSigner`: [2](#0-1) 

`requireValidTxSignatures` then enforces: [3](#0-2) 

When `nSigner == 0`, passing `signatures = []` (empty array) causes the loop to not execute, leaving `nSignatures = 0`, and `require(0 == 0)` passes unconditionally. There is no guard requiring `nSigner > 0` before proceeding.

`BaseWithdrawPool.submitFastWithdrawal` calls this function as its sole authorization gate before transferring funds: [4](#0-3) 

With the signature check bypassed, the attacker controls `sendTo` and `transferAmount` via the crafted `transaction` payload, and `handleWithdrawTransfer` executes the token transfer unconditionally.

---

### Impact Explanation

An attacker can drain all token balances held by the `WithdrawPool` contract. The attacker crafts a valid ABI-encoded `WithdrawCollateral` or `WithdrawCollateralV2` transaction pointing to an arbitrary `sendTo` address and calls `submitFastWithdrawal` with an empty `signatures` array. The `markedIdxs[idx]` replay guard is the only remaining check, but the attacker can iterate over unused `idx` values to submit multiple withdrawals for different products. All ERC-20 tokens held in the pool are at risk.

---

### Likelihood Explanation

Two realistic trigger conditions exist:

1. **Deployment with zero pubkeys**: `initialize` accepts an `initialSet` of all `Point(0,0)` entries without reverting. A misconfigured deployment leaves `nSigner == 0` from the start, and the pool is immediately exploitable before any pubkeys are assigned.

2. **Key rotation window**: The owner calls `deletePubkey` for all active signers before calling `assignPubKey` with new keys. During this window `nSigner == 0`. Since `deletePubkey` and `assignPubKey` are separate transactions, a mempool-watching attacker can front-run the `assignPubKey` call.

Neither trigger requires any special privilege from the attacker — only the ability to call `submitFastWithdrawal` with a crafted payload.

---

### Recommendation

Add an explicit guard in `requireValidTxSignatures` that reverts when `nSigner == 0`:

```solidity
require(nSigner > 0, "no signers configured");
```

This mirrors the fix described in the referenced report: require the contract's main variables to be set before treating it as operational. Analogously, `isActive`-style checks in the zBanc report prevented interactions with a partially configured converter; here, a `nSigner > 0` precondition prevents the Verifier from accepting calls before it is fully configured.

Additionally, consider adding a `nSigner > 0` invariant check inside `_initialize` of `BaseWithdrawPool` or requiring that the Verifier address passed has at least one registered signer before the pool is considered operational.

---

### Proof of Concept

```
State: nSigner == 0 (Verifier initialized with all Point(0,0), or all keys deleted)

1. Attacker crafts transaction:
   bytes memory txn = abi.encodePacked(
       uint8(IEndpoint.TransactionType.WithdrawCollateralV2),
       abi.encode(IEndpoint.SignedWithdrawCollateralV2({
           tx: WithdrawCollateralV2({
               sender: <any registered subaccount>,
               productId: <valid productId>,
               amount: <pool balance>,
               nonce: 0,
               sendTo: attacker,
               appendix: 0
           }),
           signature: ""
       }))
   );

2. Attacker calls:
   withdrawPool.submitFastWithdrawal(
       someUnusedIdx,   // idx > minIdx, not in markedIdxs
       txn,
       new bytes[](0)  // empty signatures array
   );

3. Inside requireValidTxSignatures:
   nSignatures = 0
   require(0 == nSigner)  →  require(0 == 0)  →  PASSES

4. resolveFastWithdrawal decodes sendTo = attacker, amount = pool balance

5. handleWithdrawTransfer sends full token balance to attacker
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/Verifier.sol (L16-16)
```text
    uint256 internal nSigner;
```

**File:** core/contracts/Verifier.sol (L41-48)
```text
    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```

**File:** core/contracts/Verifier.sol (L85-91)
```text
    function deletePubkey(uint256 index) public onlyOwner {
        if (!isPointNone(pubkeys[index])) {
            nSigner -= 1;
            delete pubkeys[index];
        }
        emit DeletePubkey(index);
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
