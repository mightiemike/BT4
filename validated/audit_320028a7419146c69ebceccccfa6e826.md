### Title
Unchecked Actual Received Amount in `depositCollateral` Causes Accounting Corruption for Fee-on-Transfer Tokens — (`core/contracts/Clearinghouse.sol`, `core/contracts/EndpointStorage.sol`)

---

### Summary

`Clearinghouse.depositCollateral` credits a user's on-chain balance using the pre-declared `txn.amount` from a slow-mode transaction, without ever verifying the actual token balance change of the Clearinghouse contract. The real token transfer occurs earlier, in `EndpointStorage.handleDepositTransfer`, which uses the same declared `amount` for both legs of the relay. For fee-on-transfer tokens (where the fee is deducted from the recipient on `transfer`), the Clearinghouse receives strictly less than `txn.amount`, but the user is credited the full declared amount. This permanently inflates the protocol's internal accounting above its real token holdings, leading to insolvency.

---

### Finding Description

The deposit flow in Nado is split across two steps:

**Step 1 — Token transfer (Endpoint.sol:144–148, EndpointStorage.sol:111–119)**

When a user calls `Endpoint.depositCollateralWithReferral`, `handleDepositTransfer` is invoked:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    safeTransferFrom(token, from, amount);                    // user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount);    // Endpoint → Clearinghouse
}
```

The declared `amount` is used verbatim for both legs. For a fee-on-transfer token where the fee is deducted from the recipient on `transfer`, the second leg (`safeTransferTo`) succeeds (the call returns `true`), but the Clearinghouse receives only `amount − fee`.

**Step 2 — Balance credit (Clearinghouse.sol:193–209)**

The same `amount` is stored in the slow-mode transaction queue (Endpoint.sol:152–165). When the sequencer later processes it, `Clearinghouse.depositCollateral` is called:

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

`txn.amount` is the pre-declared value. There is no balance snapshot before/after the transfer, and no check that the Clearinghouse actually received `txn.amount` tokens. The user is credited `amountRealized` based on the declared amount, not the actual received amount.

**Root cause**: The protocol assumes a 1:1 correspondence between the declared deposit amount and the tokens actually received by the Clearinghouse. This invariant is broken for any token that deducts a fee from the recipient on `transfer`.

---

### Impact Explanation

Each deposit of a fee-on-transfer token inflates the user's credited balance by `fee` units above what the Clearinghouse actually holds. Repeated deposits accumulate this discrepancy. Eventually, the sum of all credited balances exceeds the Clearinghouse's real token holdings. When users attempt to withdraw, the protocol cannot satisfy all claims — a classic insolvency / accounting corruption outcome. Any user who deposits early and withdraws before the shortfall is realized effectively extracts value from later depositors.

---

### Likelihood Explanation

The trigger requires a supported collateral token with a fee-on-transfer mechanism (fee deducted from recipient on `transfer`). The protocol does not whitelist tokens by transfer behavior, and several real-world tokens (e.g., USDT with fee enabled, certain rebasing or tax tokens) exhibit this property. The entry path is fully permissionless: any user can call `Endpoint.depositCollateral` or `Endpoint.depositCollateralWithReferral`. No privileged role is required. Likelihood is **low-to-medium** depending on which tokens are listed, but the impact is high once triggered.

---

### Recommendation

In `Clearinghouse.depositCollateral`, replace the use of `txn.amount` with the actual balance delta:

```solidity
uint128 balanceBefore = _balanceOf(_tokenAddress(txn.productId));
// ... (transfer already occurred; snapshot is taken here)
uint128 balanceAfter = _balanceOf(_tokenAddress(txn.productId));
uint128 actualReceived = balanceAfter - balanceBefore;
int128 amountRealized = int128(actualReceived) * int128(multiplier);
```

Alternatively, take the balance snapshot in `handleDepositTransfer` (EndpointStorage.sol) and pass the actual received amount through the slow-mode transaction payload instead of the user-declared amount.

---

### Proof of Concept

1. A fee-on-transfer token `T` (fee = 1%, deducted from recipient on `transfer`) is listed as a supported collateral product.
2. Attacker calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(T, attacker, 1000e6)` → Endpoint receives `1000e6`.
   - `safeTransferTo(T, clearinghouse, 1000e6)` → Clearinghouse receives `990e6` (1% fee deducted from recipient). Call returns `true`.
4. Slow-mode tx is queued with `amount = 1000e6`.
5. Sequencer processes the slow-mode tx; `Clearinghouse.depositCollateral` is called with `txn.amount = 1000e6`.
6. `amountRealized = 1000e6 * multiplier` is credited to the attacker's subaccount — **10e6 more than the Clearinghouse actually received**.
7. Attacker immediately withdraws `1000e6` worth of collateral; Clearinghouse only holds `990e6` → shortfall of `10e6` is borne by the protocol.
8. Repeated across many deposits, the protocol becomes insolvent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/contracts/Endpoint.sol (L144-165)
```text
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
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
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
