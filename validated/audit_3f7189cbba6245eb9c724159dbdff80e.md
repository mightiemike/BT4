### Title
Fee-on-Transfer Token Accounting Mismatch in `handleDepositTransfer` Drains Endpoint Token Reserves or Inflates Subaccount Credit - (`core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer` performs two sequential ERC-20 transfers using the same nominal `amount`. For fee-on-transfer tokens, the first transfer (`user → Endpoint`) delivers `amount - fee` to the Endpoint, but the second transfer (`Endpoint → Clearinghouse`) attempts to forward the full `amount`. This either reverts (DoS on all deposits for that token) or silently drains tokens already held by the Endpoint from prior deposits, while simultaneously crediting the depositor's subaccount with the full `amount` rather than the net received amount.

---

### Finding Description

`EndpointStorage.handleDepositTransfer` is the single internal function that moves tokens during every deposit flow:

```solidity
// core/contracts/EndpointStorage.sol  lines 111-119
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);               // ① user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount); // ② Endpoint → Clearinghouse
}
```

For a fee-on-transfer token, step ① delivers `amount - fee` to the Endpoint. Step ② then attempts to forward the original `amount`, which the Endpoint does not hold. Two outcomes are possible:

- **Revert / DoS**: If the Endpoint holds no surplus of that token, step ② reverts, permanently blocking all deposits for that product.
- **Token drain**: If the Endpoint holds a surplus (e.g., from a prior failed or partial deposit), step ② silently consumes those surplus tokens to cover the shortfall, stealing funds that belong to other users.

After `handleDepositTransfer` returns, `depositCollateralWithReferral` enqueues a `DepositCollateral` slow-mode transaction carrying the full `amount`:

```solidity
// core/contracts/Endpoint.sol  lines 152-165
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    ...
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(
            DepositCollateral({
                sender: subaccount,
                productId: productId,
                amount: amount          // ← nominal amount, not net received
            })
        )
    )
});
```

When the sequencer later processes this slow-mode transaction via `EndpointTx → clearinghouse.depositCollateral(txn)`, the clearinghouse credits the subaccount with the full `amount`, not `amount - fee`. The depositor therefore receives inflated credit backed by tokens that were never actually received.

The same root cause is present in `DirectDepositV1.creditDeposit`, which reads the DDA's token balance and passes it verbatim as `amount` to `endpoint.depositCollateralWithReferral`:

```solidity
// core/contracts/DirectDepositV1.sol  lines 90-98
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),   // ← full balance, fee will be deducted in transit
        "-1"
    );
}
```

Here the DDA holds exactly `balance` tokens. After `safeTransferFrom(DDA, Endpoint, balance)`, the Endpoint receives `balance - fee`. The subsequent `safeTransferTo(Endpoint, clearinghouse, balance)` will revert or drain surplus tokens.

---

### Impact Explanation

**Accounting corruption / collateral theft**: Any user who deposits a fee-on-transfer token receives subaccount credit for the full nominal `amount` while the protocol only holds `amount - fee`. Repeated deposits inflate the protocol's internal accounting relative to its real token reserves, eventually making the system insolvent for that product.

**Token drain**: If the Endpoint contract holds a surplus of the fee-on-transfer token (e.g., from a prior failed deposit or direct transfer), each deposit call silently consumes that surplus to cover the fee shortfall, stealing funds that belong to other users or the protocol.

**DoS**: If no surplus exists, step ② reverts, blocking all deposits for that product until surplus is manually injected.

---

### Likelihood Explanation

`depositCollateral` and `depositCollateralWithReferral` are permissionless external entry points callable by any user. Any fee-on-transfer token registered as a spot product triggers the bug immediately on the first deposit. The `DirectDepositV1.creditDeposit` path is also callable by anyone (`external`, no access control on the caller side of the deposit). No privileged access is required.

---

### Recommendation

Measure the actual received amount by comparing balances before and after the inbound transfer, then use that net amount for the outbound transfer and for the queued `DepositCollateral` amount:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balanceBefore = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 received = token.balanceOf(address(this)) - balanceBefore;
    safeTransferTo(token, address(clearinghouse), received);
    // return `received` to callers so the slow-mode DepositCollateral
    // transaction is queued with the net amount, not the nominal amount.
}
```

`depositCollateralWithReferral` must then use the returned `received` value when constructing the `DepositCollateral` slow-mode transaction, so the clearinghouse credits only what was actually received.

---

### Proof of Concept

1. Register a fee-on-transfer token (e.g., 1% fee per transfer) as a spot product.
2. Call `Endpoint.depositCollateral(subaccountName, productId, 1000e18)`.
3. Inside `handleDepositTransfer`:
   - `safeTransferFrom(token, user, 1000e18)` → Endpoint receives `990e18`.
   - `safeTransferTo(token, clearinghouse, 1000e18)` → **reverts** (Endpoint only has `990e18`), or drains `10e18` from existing Endpoint surplus.
4. If the drain path executes, the slow-mode queue records `amount = 1000e18`; when processed, the clearinghouse credits the subaccount with `1000e18` while the clearinghouse only received `990e18` (or less, if the surplus was itself fee-reduced).
5. Repeat to progressively drain the Endpoint's surplus and inflate subaccount balances beyond the protocol's real reserves.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** core/contracts/DirectDepositV1.sol (L83-99)
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
