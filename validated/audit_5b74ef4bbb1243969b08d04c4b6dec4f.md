### Title
Fee-on-Transfer Token Deposits Corrupt Collateral Accounting via Unadjusted `amount` in `handleDepositTransfer` — (`core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer()` uses the caller-supplied `amount` for both the pull from the depositor and the push to the clearinghouse. With a fee-on-transfer token the Endpoint receives `amount − fee` from the user but forwards `amount` to the clearinghouse, consuming `fee` from the Endpoint's own accumulated balance. The slow-mode transaction queued immediately after records the original `amount`, so when the sequencer processes it the clearinghouse credits the user with `amount` rather than the actual net-received `amount − fee`. The user gains `fee` of collateral credit they never deposited; the Endpoint's fee reserve is silently drained.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` calls `handleDepositTransfer` and then enqueues a slow-mode `DepositCollateral` transaction, both using the same caller-supplied `amount`:

```solidity
// Endpoint.sol  lines 144-165
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)          // ← original amount
);
...
slowModeTxs[...] = SlowModeTx({
    ...
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(
            DepositCollateral({
                sender: subaccount,
                productId: productId,
                amount: amount   // ← same original amount
            })
        )
    )
});
``` [1](#0-0) 

Inside `handleDepositTransfer` the same `amount` is used for both legs of the transfer:

```solidity
// EndpointStorage.sol  lines 111-119
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);              // receives amount − fee
    safeTransferTo(token, address(clearinghouse), amount); // sends amount (not amount − fee)
}
``` [2](#0-1) 

With a fee-on-transfer token:

| Step | Action | Endpoint balance delta |
|---|---|---|
| `safeTransferFrom` | User sends `amount`; Endpoint receives `amount − fee` | `+(amount − fee)` |
| `safeTransferTo` | Endpoint sends `amount` to clearinghouse | `−amount` |
| **Net** | | **`−fee`** (drawn from pre-existing Endpoint balance) |

The Endpoint accumulates quote-token balance from slow-mode fees via `chargeSlowModeFee`:

```solidity
// EndpointStorage.sol  lines 83-93
function chargeSlowModeFee(IERC20Base token, address from) internal virtual {
    require(address(token) != address(0));
    token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
}
``` [3](#0-2) 

When the slow-mode transaction is later executed, `processSlowModeTransactionImpl` passes the original `amount` directly to `clearinghouse.depositCollateral`:

```solidity
// EndpointTx.sol  lines 209-216
if (txType == IEndpoint.TransactionType.DepositCollateral) {
    IEndpoint.DepositCollateral memory txn = abi.decode(
        transaction[1:], (IEndpoint.DepositCollateral)
    );
    validateSender(txn.sender, sender);
    _recordSubaccount(txn.sender);
    clearinghouse.depositCollateral(txn);   // credits txn.amount = original amount
}
``` [4](#0-3) 

The clearinghouse credits the subaccount with `amount` even though the user only contributed `amount − fee` of real value. The `fee` shortfall is silently absorbed from the Endpoint's own token reserve.

---

### Impact Explanation

**Accounting corruption / collateral inflation**: A depositor using a fee-on-transfer collateral token is credited with `amount` of collateral while only `amount − fee` was transferred from their wallet. The difference (`fee`) is taken from the Endpoint's accumulated balance. Repeated deposits drain the Endpoint's fee reserve and inflate the depositor's collateral balance beyond what they actually provided, undermining the solvency invariant that every credited unit of collateral is backed by a real on-chain token.

If the Endpoint holds no pre-existing balance of the token, `safeTransferTo` reverts and the deposit is entirely blocked (DoS for that token class).

---

### Likelihood Explanation

Any unprivileged user can call `depositCollateral` or `depositCollateralWithReferral` directly. The Endpoint accumulates quote-token balance from slow-mode fees; if the quote token or any other supported collateral token carries a transfer fee, the precondition (Endpoint holds ≥ `fee` of that token) is met organically over normal protocol operation. The attacker needs only to deposit a supported fee-on-transfer token.

---

### Recommendation

In `handleDepositTransfer`, snapshot the Endpoint's token balance before and after `safeTransferFrom` and use the actual received amount for the push to the clearinghouse and for the slow-mode transaction record:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balanceBefore = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 actualReceived = token.balanceOf(address(this)) - balanceBefore;
    safeTransferTo(token, address(clearinghouse), actualReceived);
    // return actualReceived so callers can record the correct amount
}
```

The caller (`depositCollateralWithReferral`) must then use `actualReceived` instead of `amount` when constructing the `DepositCollateral` slow-mode transaction.

---

### Proof of Concept

1. Deploy a collateral token with a 1 % transfer fee and register it as a supported spot product.
2. Ensure the Endpoint holds ≥ 10 units of that token (e.g., from accumulated slow-mode fees paid in the same token).
3. Call `depositCollateral(subaccountName, productId, 1000)`.
4. `safeTransferFrom` pulls 1000 from the user; Endpoint receives 990 (1 % fee deducted).
5. `safeTransferTo` pushes 1000 to the clearinghouse, consuming 10 from the Endpoint's own balance.
6. Slow-mode tx is queued with `amount = 1000`.
7. Sequencer executes the slow-mode tx; `clearinghouse.depositCollateral` credits the subaccount with 1000.
8. **Result**: user deposited 990 net but holds 1000 of collateral credit — a free gain of 10 units at the Endpoint's expense. Repeating this drains the Endpoint's reserve and inflates collateral across the system.

### Citations

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

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
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

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```
