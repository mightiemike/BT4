### Title
`handleDepositTransfer` Does Not Verify Actual Received Token Amount, Enabling Solvency Accounting Corruption with Fee-on-Transfer Tokens — (File: `core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` calls `safeTransferFrom` to pull tokens from the depositor, then immediately calls `safeTransferTo` forwarding the original nominal `amount` to the Clearinghouse — without ever checking the balance before and after the inbound transfer. With a fee-on-transfer token, the Clearinghouse receives less than `amount`, but the user's subaccount is credited with the full `amount`, inflating the protocol's internal accounting relative to its actual token holdings.

---

### Finding Description

The deposit entry point `depositCollateralWithReferral` in `Endpoint.sol` calls `handleDepositTransfer`: [1](#0-0) 

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);       // pulls `amount` from user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount); // pushes `amount` from Endpoint → Clearinghouse
}
```

`safeTransferFrom` is implemented via a low-level `.call()` in `ERC20Helper` and only checks that the call returned success — it does **not** verify the actual balance delta: [2](#0-1) 

With a fee-on-transfer token (fee rate `f`):

1. `safeTransferFrom(token, user, endpoint, amount)` → Endpoint receives `amount × (1 - f)`. The Endpoint is short `amount × f`.
2. `safeTransferTo(token, endpoint, clearinghouse, amount)` → Endpoint tries to forward the full `amount`. This succeeds only if the Endpoint holds a pre-existing balance ≥ `amount × f` (accumulated from `chargeSlowModeFee` calls in the quote token). The Clearinghouse receives `amount × (1 - f)` (fee taken again on the outbound leg).
3. The slow-mode transaction queue records the original `amount`: [3](#0-2) 

4. When the sequencer processes the slow-mode tx, `Clearinghouse.depositCollateral` credits the user with the full `amount × multiplier`: [4](#0-3) 

The Clearinghouse's actual token balance is `amount × (1 - f)`, but the user's subaccount balance is `amount`. The discrepancy (`amount × f`) is a permanent underfunding of the Clearinghouse per deposit.

---

### Impact Explanation

Each deposit of a fee-on-transfer token inflates the sum of all subaccount balances in `SpotEngine` relative to the Clearinghouse's real token holdings by `amount × f`. Over many deposits, the Clearinghouse becomes insolvent: the last users to withdraw cannot redeem their full credited balance because the underlying tokens are not there. This is a direct solvency/accounting corruption of the protocol's collateral layer.

Additionally, the Endpoint's pre-existing balance (from slow-mode fees) is silently drained by `amount × f` per deposit to cover the shortfall in step 2, eroding the protocol's fee reserves.

---

### Likelihood Explanation

A fee-on-transfer token must be registered as a spot product (an admin action), and the Endpoint must hold a pre-existing balance of that token (naturally accumulated via `chargeSlowModeFee` for the quote token). Both conditions are realistic: some legitimate tokens (e.g., rebasing or tax tokens) carry transfer fees, and an operator may list one without auditing its transfer mechanics. The Endpoint always accumulates quote-token balance from slow-mode fees, satisfying the second condition for the quote product. Any unprivileged user can then trigger the corruption simply by calling `depositCollateralWithReferral`.

---

### Recommendation

Check the Endpoint's token balance before and after `safeTransferFrom` and use the actual received delta — not the caller-supplied `amount` — for both the forwarding transfer and the slow-mode transaction record:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balanceBefore = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 actualReceived = token.balanceOf(address(this)) - balanceBefore;
    require(actualReceived == amount, ERR_TRANSFER_FAILED); // or use actualReceived downstream
    safeTransferTo(token, address(clearinghouse), actualReceived);
}
```

The `DepositCollateral` slow-mode transaction should record `actualReceived` (converted to native decimals) rather than the user-supplied `amount`. Alternatively, consider explicitly disallowing fee-on-transfer tokens at product registration time.

---

### Proof of Concept

1. Admin registers a spot product whose token is a fee-on-transfer ERC-20 (1% fee per transfer).
2. The Endpoint has accumulated ≥ 1 USDC-equivalent of that token from prior `chargeSlowModeFee` calls.
3. Attacker calls `depositCollateralWithReferral(subaccount, productId, 100e6)`.
4. `handleDepositTransfer` executes:
   - `safeTransferFrom`: Endpoint receives 99e6 (1e6 taken as fee). ✓ passes `ERC20Helper` check.
   - `safeTransferTo`: Endpoint sends 100e6 to Clearinghouse (uses 1e6 from pre-existing balance). Clearinghouse receives 99e6 (1e6 taken as fee again). ✓ passes.
5. Slow-mode tx records `amount = 100e6`.
6. Sequencer processes tx → `Clearinghouse.depositCollateral` credits subaccount with `100e6 × multiplier`.
7. Clearinghouse holds 99e6 tokens but owes 100e6 to the subaccount. Discrepancy: 1e6 per deposit, compounding with every subsequent deposit.

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

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/Endpoint.sol (L152-166)
```text
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
```

**File:** core/contracts/Clearinghouse.sol (L205-208)
```text
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```
