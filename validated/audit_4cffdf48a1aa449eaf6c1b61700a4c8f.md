### Title
Deposited Collateral Not Credited to Subaccount During Slow Mode Delay, Causing Continued Borrow Interest Accrual — (`core/contracts/Endpoint.sol`)

---

### Summary

In `Endpoint.depositCollateralWithReferral()`, user funds are seized from the caller immediately via `handleDepositTransfer()`, but the corresponding subaccount balance credit is deferred behind a mandatory 3-day slow mode queue. During this window, if the depositing subaccount carries a negative spot balance (a borrow), `SpotEngineState._updateState()` continues to compound borrow interest against the full outstanding borrow — even though the protocol already holds the repayment funds. This is a direct structural analog to the Wildcat M-09 finding: assets are in the contract, but the accounting state is not updated until a timer expires.

---

### Finding Description

`depositCollateralWithReferral()` in `Endpoint.sol` executes two logically coupled steps in a temporally decoupled way:

**Step 1 — Immediate fund seizure** (lines 144–148):
```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
```

**Step 2 — Deferred balance credit** (lines 152–166): the deposit is enqueued as a `SlowModeTx` with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days):
```solidity
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    ...
    tx: abi.encodePacked(uint8(TransactionType.DepositCollateral), ...)
});
```

The balance is only credited when `processSlowModeTransactionImpl()` in `EndpointTx.sol` (lines 209–216) eventually calls `clearinghouse.depositCollateral(txn)`, which calls `spotEngine.updateBalance(txn.productId, txn.sender, amountRealized)`.

Meanwhile, `SpotEngineState._updateState()` (lines 52–178) continuously compounds the `cumulativeBorrowsMultiplierX18` for every product with outstanding borrows. Any subaccount with a negative balance (borrow) has its effective debt grow with each state update, regardless of whether the protocol already holds the repayment funds.

The user cannot force early execution: `_executeSlowModeTransaction()` enforces `fromSequencer || (txn.executableAt <= block.timestamp)` — only the sequencer can bypass the 3-day gate. If the sequencer is unavailable or selectively censoring this user's deposit while processing other transactions (the exact scenario slow mode is designed to protect against), `_updateState()` is still triggered by those other transactions, accruing borrow interest on the full outstanding balance.

---

### Impact Explanation

A user with an active borrow who calls `depositCollateral()` to repay it loses funds to borrow interest for up to 3 days after their tokens have already been transferred to the protocol. The corrupted state is the subaccount's normalized borrow balance: `balances[productId][subaccount].amountNormalized` remains negative (borrow) and continues to be multiplied by a growing `cumulativeBorrowsMultiplierX18`, inflating the effective debt. The user overpays interest proportional to their borrow size and the duration of the delay.

---

### Likelihood Explanation

The slow mode mechanism is explicitly designed for sequencer downtime or censorship. Any user who deposits collateral to repay a borrow during a sequencer outage — the primary use case for slow mode — will experience this. The attacker-controlled entry path is simply calling `depositCollateral()` or `depositCollateralWithReferral()` on `Endpoint.sol` while holding a negative spot balance. No special privileges are required.

---

### Recommendation

When `depositCollateralWithReferral()` enqueues a `DepositCollateral` slow mode transaction, it should immediately apply the balance credit to the subaccount (mirroring what `clearinghouse.depositCollateral()` does) rather than deferring it. Alternatively, the slow mode execution path should be made callable by the depositor immediately for `DepositCollateral` transactions (removing the 3-day gate for this specific type), since the funds are already in custody and there is no economic reason to delay the accounting update.

---

### Proof of Concept

1. Alice has a borrow of 10,000 USDC in the spot engine (negative `amountNormalized` in `balances[QUOTE_PRODUCT_ID][alice]`).
2. Alice calls `Endpoint.depositCollateral(subaccountName, QUOTE_PRODUCT_ID, 10_000e6)`.
3. `handleDepositTransfer` immediately pulls 10,000 USDC from Alice into the protocol. [1](#0-0) 
4. A `SlowModeTx` is queued with `executableAt = block.timestamp + 259200` (3 days). Alice's subaccount balance is **not** updated. [2](#0-1) 
5. The sequencer is down. Over the next 3 days, other on-chain activity triggers `_updateState()` for the quote product, growing `cumulativeBorrowsMultiplierX18`. Alice's effective debt = `amountNormalized × cumulativeBorrowsMultiplierX18` increases continuously. [3](#0-2) 
6. After 3 days, Alice calls `executeSlowModeTransaction()`. Only now does `processSlowModeTransactionImpl` call `clearinghouse.depositCollateral()` → `spotEngine.updateBalance()`, crediting her balance. [4](#0-3) 
7. Alice has paid 3 days of borrow interest on 10,000 USDC even though the protocol held her repayment funds from step 3. The `SLOW_MODE_TX_DELAY` constant hardcodes this 3-day window. [5](#0-4)

### Citations

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
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

**File:** core/contracts/SpotEngineState.sol (L52-98)
```text
    function _updateState(
        uint32 productId,
        State memory state,
        uint128 dt
    ) internal {
        int128 borrowRateMultiplierX18;
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
        int128 minDepositRateX18;
        {
            Config memory config = configs[productId];

            // annualized borrower rate
            int128 borrowerRateX18 = config.interestFloorX18;
            if (utilizationRatioX18 == 0) {
                // setting borrowerRateX18 to 0 here has the property that
                // adding a product at the beginning of time and not using it until time T
                // results in the same state as adding the product at time T
                borrowerRateX18 = 0;
            } else if (utilizationRatioX18 < config.interestInflectionUtilX18) {
                borrowerRateX18 += config
                    .interestSmallCapX18
                    .mul(utilizationRatioX18)
                    .div(config.interestInflectionUtilX18);
            } else {
                borrowerRateX18 +=
                    config.interestSmallCapX18 +
                    config.interestLargeCapX18.mul(
                        (
                            (utilizationRatioX18 -
                                config.interestInflectionUtilX18).div(
                                    ONE - config.interestInflectionUtilX18
                                )
                        )
                    );
            }

            // convert to per second
            borrowerRateX18 = borrowerRateX18.div(
                MathSD21x18.fromInt(31536000)
            );
            borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
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

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
