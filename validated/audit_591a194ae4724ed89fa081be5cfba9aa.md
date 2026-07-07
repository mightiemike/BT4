### Title
CEI Violation in `withdrawCollateral` Enables ERC777 Reentrancy to Double-Spend Collateral - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` performs an external token transfer before updating the subaccount's balance in `SpotEngine`. If the collateral token is ERC777 (or any token with a transfer hook), the recipient contract can reenter `Endpoint.depositCollateralWithReferral` during the callback, queuing a deposit credit for the same tokens before the debit is applied. The sequencer later processes the queued deposit, leaving the attacker with both the withdrawn tokens and a restored on-chain balance.

---

### Finding Description

In `Clearinghouse.withdrawCollateral`, the execution order is:

1. **Line 408** — `handleWithdrawTransfer(token, sendTo, amount, idx)` — external call that ultimately calls `token.safeTransfer(sendTo, amount)` via `WithdrawPool`
2. **Line 412** — `spotEngine.updateBalance(productId, sender, amountRealized)` — balance debit
3. **Line 419** — `require(getHealth(sender, healthType) >= 0, ...)` — health check [1](#0-0) 

The `handleWithdrawTransfer` internal function first transfers tokens to `withdrawPool`, then calls `BaseWithdrawPool.submitWithdrawal`, which calls `token.safeTransfer(to, amount)` to deliver tokens to `sendTo`. [2](#0-1) 

`BaseWithdrawPool.submitWithdrawal` marks `markedIdxs[idx] = true` before the transfer, preventing replay of the same withdrawal index — but this does **not** prevent reentrancy into a different entry point (`depositCollateralWithReferral`). [3](#0-2) 

There are **no reentrancy guards** anywhere in the codebase (`nonReentrant` / `ReentrancyGuard` are absent).

`Endpoint.depositCollateralWithReferral` is a public, permissionless function. It calls `handleDepositTransfer` which pulls tokens from `msg.sender` and forwards them to the clearinghouse, then enqueues a slow-mode `DepositCollateral` transaction. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

An attacker can effectively double-spend collateral:

- They start with `N` ERC777 tokens credited in their subaccount.
- After the attack, they hold `N` tokens in their wallet **and** have `N` tokens re-credited to their subaccount.
- Repeating this drains the clearinghouse's token reserves, corrupting the solvency invariant that on-chain token balances must cover all subaccount credits.

This is a direct asset theft / accounting corruption impact.

---

### Likelihood Explanation

- The attacker only needs to control a contract address as `sendTo` and use a supported ERC777 collateral token (or any token with a `transfer` hook).
- No privileged access, governance capture, or sequencer compromise is required.
- The entry point (`depositCollateralWithReferral`) is fully public and permissionless.
- The sequencer's role is only to process the queued deposit later — it is not a blocker.

---

### Recommendation

Move `spotEngine.updateBalance` and the health check **before** `handleWithdrawTransfer`, following the Check-Effect-Interact pattern:

```solidity
function withdrawCollateral(...) public virtual onlyEndpoint {
    // 1. Checks
    require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
    require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
    ...

    // 2. Effects
    int128 amountRealized = -int128(amount) * int128(multiplier);
    spotEngine.updateBalance(productId, sender, amountRealized);
    spotEngine.assertUtilization(productId);
    require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);

    // 3. Interact
    handleWithdrawTransfer(token, sendTo, amount, idx);
    emit ModifyCollateral(amountRealized, sender, productId);
}
```

Alternatively, add OpenZeppelin's `ReentrancyGuard` (`nonReentrant`) to `withdrawCollateral` and any other function that performs external calls before state updates.

---

### Proof of Concept

1. Attacker deploys `AttackerContract` with an ERC777 `tokensReceived` hook. The hook calls `Endpoint.depositCollateralWithReferral(attackerSubaccount, productId, amount, "")` and approves the Endpoint beforehand.
2. Attacker deposits `100` ERC777 tokens into their subaccount via `depositCollateral`.
3. Attacker submits a withdrawal for `100` tokens with `sendTo = address(AttackerContract)`.
4. Sequencer calls `Clearinghouse.withdrawCollateral(attackerSubaccount, productId, 100, AttackerContract, idx)`.
5. `handleWithdrawTransfer` → `WithdrawPool.submitWithdrawal` → `token.safeTransfer(AttackerContract, 100)` triggers `tokensReceived`.
6. Inside the hook: `AttackerContract` calls `Endpoint.depositCollateralWithReferral(attackerSubaccount, productId, 100, "")`. The 100 tokens are pulled from `AttackerContract` into the clearinghouse. A slow-mode `DepositCollateral` tx is queued.
7. Hook returns. Execution resumes in `withdrawCollateral`: `spotEngine.updateBalance` debits `100` from the subaccount balance (now `0`). Health check passes.
8. Sequencer later processes the queued slow-mode deposit: `spotEngine.updateBalance` credits `+100` to the subaccount.
9. **Net result**: Attacker's wallet holds `100` tokens AND their subaccount balance is `100` — a net gain of `100` tokens extracted from the protocol's reserves.

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L408-419)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

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

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```
