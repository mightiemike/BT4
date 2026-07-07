### Title
USDC-Blacklisted `sendTo` Permanently Locks Funds and Halts Sequencer via Uncaught Revert in `BaseWithdrawPool.handleWithdrawTransfer()` — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.handleWithdrawTransfer()` calls `token.safeTransfer(to, amount)` with no error handling. If the `to` address is blacklisted by USDC at the time the sequencer submits the withdrawal on-chain, the transfer reverts and propagates through the entire call chain — `submitWithdrawal` → `Clearinghouse.handleWithdrawTransfer` → `Clearinghouse.withdrawCollateral` → `Endpoint._delegatecallEndpointTx`. Because `nSubmissions` is strictly sequential and is only incremented on success, the sequencer cannot advance past this transaction. The result is a complete protocol halt and permanent inaccessibility of the affected user's funds.

---

### Finding Description

The withdrawal execution path in Nado is:

```
Endpoint.submitTransactions()
  └─ _delegatecallEndpointTx()  [propagates reverts]
       └─ EndpointTx.processTransactionImpl()
            └─ clearinghouse.withdrawCollateral(sender, productId, amount, sendTo, nSubmissions)
                 └─ Clearinghouse.handleWithdrawTransfer(token, sendTo, amount, idx)
                      ├─ token.safeTransfer(withdrawPool, amount)
                      └─ BaseWithdrawPool(withdrawPool).submitWithdrawal(token, sendTo, amount, idx)
                           └─ BaseWithdrawPool.handleWithdrawTransfer(token, sendTo, amount)
                                └─ token.safeTransfer(sendTo, amount)   ← REVERT if blacklisted
```

The terminal call is in `BaseWithdrawPool.handleWithdrawTransfer()`: [1](#0-0) 

```solidity
function handleWithdrawTransfer(
    IERC20Base token,
    address to,
    uint128 amount
) internal virtual {
    token.safeTransfer(to, uint256(amount));   // no try/catch
}
```

`safeTransfer` is implemented in `ERC20Helper` as a low-level `.call` with a hard `require`: [2](#0-1) 

If USDC's `notBlacklisted` modifier causes the `transfer` to revert, `success` is `false`, and the `require` fires `ERR_TRANSFER_FAILED`. This revert is not caught anywhere in the chain.

The Endpoint propagates the revert unconditionally: [3](#0-2) 

The submission index guard enforces strict sequentiality — `nSubmissions` is only incremented on a successful transaction: [4](#0-3) 

There is no mechanism to skip or tombstone a failed withdrawal. Once the sequencer's off-chain state has committed to processing this withdrawal (decrementing the user's balance), it cannot advance `nSubmissions` past it without the on-chain transaction succeeding. Every subsequent batch that includes this withdrawal will revert.

The vulnerability is reachable via two on-chain entry points:

1. **`WithdrawCollateral` (V1)** — `sendTo` defaults to `address(uint160(bytes20(sender)))`. If the sender's wallet address is blacklisted by USDC, the withdrawal fails. [5](#0-4) 

2. **`WithdrawCollateralV2`** — `sendTo` is an explicit user-supplied address. If that address is blacklisted, the withdrawal fails. [6](#0-5) 

A third path, `rebalanceXWithdraw`, also calls `withdrawCollateral` with a caller-supplied `sendTo`: [7](#0-6) 

---

### Impact Explanation

**High.** Two distinct asset/state deltas are corrupted:

1. **User funds permanently inaccessible**: The user's on-chain balance in `SpotEngine` is never decremented (the revert prevents `spotEngine.updateBalance`), but the sequencer's off-chain state has already committed the deduction. The user cannot re-submit the withdrawal because the sequencer's off-chain ledger shows a zero balance. The funds sit in the `Clearinghouse` with no reachable withdrawal path. [8](#0-7) 

2. **Full sequencer halt**: Because `nSubmissions` cannot advance, every subsequent transaction for every user in the protocol is blocked. This is a complete liveness failure, not just a single-user impact. [9](#0-8) 

---

### Likelihood Explanation

**Medium.** USDC blacklisting is an operational reality enforced by Circle. The window of exposure is the latency between the sequencer committing the withdrawal off-chain and the on-chain batch being mined. For `WithdrawCollateralV2`, a user can freely specify any `sendTo` address, including one that is already under regulatory scrutiny and may be blacklisted imminently. The scenario does not require any privileged action by the attacker — only that Circle blacklists the destination address during the processing window.

---

### Recommendation

Wrap the `token.safeTransfer(to, amount)` call in `BaseWithdrawPool.handleWithdrawTransfer()` in a try/catch block (using a low-level `.call` pattern, since Solidity `try/catch` only works on external calls). On failure, record the stuck amount in a per-address mapping:

```solidity
mapping(address => mapping(address => uint256)) public blacklistedFunds; // token => recipient => amount

function handleWithdrawTransfer(IERC20Base token, address to, uint128 amount) internal virtual {
    (bool success, ) = address(token).call(
        abi.encodeWithSelector(IERC20Base.transfer.selector, to, uint256(amount))
    );
    if (!success) {
        blacklistedFunds[address(token)][to] += uint256(amount);
        emit WithdrawBlacklisted(address(token), to, uint256(amount));
    }
}

function withdrawBlacklistedFunds(address token, address to) external {
    uint256 amount = blacklistedFunds[token][to];
    require(amount > 0);
    blacklistedFunds[token][to] = 0;
    IERC20Base(token).safeTransfer(to, amount);
}
```

This mirrors the fix applied in the referenced `L1OpUSDCBridgeAdapter` report (commit `eb625f95`). The sequencer can then continue advancing `nSubmissions` even when a destination address is blacklisted, and the affected user can recover funds once removed from the blacklist.

---

### Proof of Concept

1. Alice holds 10,000 USDC in Nado and signs a `WithdrawCollateralV2` transaction with `sendTo = BOB`.
2. The sequencer processes the withdrawal off-chain, decrementing Alice's balance in its local state.
3. Before the sequencer submits the on-chain batch, Circle blacklists `BOB` in the USDC contract.
4. The sequencer submits the batch. `Clearinghouse.withdrawCollateral` is called, which calls `BaseWithdrawPool.submitWithdrawal`, which calls `handleWithdrawTransfer`, which calls `token.safeTransfer(BOB, 10000e6)`.
5. USDC's `notBlacklisted(BOB)` modifier reverts. `ERC20Helper.safeTransfer` fires `ERR_TRANSFER_FAILED`.
6. The revert propagates through `submitWithdrawal` → `Clearinghouse.handleWithdrawTransfer` → `Clearinghouse.withdrawCollateral` → `_delegatecallEndpointTx` → `submitTransactions`. The entire batch reverts.
7. `nSubmissions` is not incremented. The sequencer cannot submit any subsequent batch without including this withdrawal, which will keep reverting.
8. Alice's on-chain balance is never decremented (revert prevented it), but the sequencer's off-chain ledger shows zero — Alice cannot re-request the withdrawal.
9. All other users' transactions are also blocked because `nSubmissions` is frozen. [1](#0-0) [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/Endpoint.sol (L68-84)
```text
    function _delegatecallEndpointTx(bytes memory callData)
        internal
        returns (bytes memory)
    {
        require(endpointTx != address(0), "Endpoint Tx not set");
        (bool success, bytes memory result) = endpointTx.delegatecall(callData);
        if (!success) {
            if (result.length == 0) {
                revert();
            }
            // solhint-disable-next-line no-inline-assembly
            assembly {
                revert(add(result, 0x20), mload(result))
            }
        }
        return result;
    }
```

**File:** core/contracts/Endpoint.sol (L86-88)
```text
    function validateSubmissionIdx(uint64 idx) private view {
        require(idx == nSubmissions, ERR_INVALID_SUBMISSION_INDEX);
    }
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Clearinghouse.sol (L327-343)
```text
    function rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions)
        external
        onlyEndpoint
    {
        IEndpoint.RebalanceXWithdraw memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.RebalanceXWithdraw)
        );

        withdrawCollateral(
            X_ACCOUNT,
            txn.productId,
            txn.amount,
            txn.sendTo,
            nSubmissions
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```
