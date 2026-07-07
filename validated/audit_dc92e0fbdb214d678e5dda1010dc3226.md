### Title
No Maximum Withdrawal Sentinel in `WithdrawCollateral` Causes Repeated Slow-Mode Failures and Extended Fund Locks - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`withdrawCollateral` in `Clearinghouse.sol` enforces two constraints — a utilization ceiling via `assertUtilization` and a health floor via `getHealth` — but neither `WithdrawCollateral` nor `WithdrawCollateralV2` carry a "withdraw-max" sentinel. When pool utilization or subaccount health is near its limit, a user submitting a slow-mode withdrawal must guess the exact safe amount. Because failed slow-mode transactions are silently dropped after the mandatory 3-day `SLOW_MODE_TX_DELAY`, each wrong guess costs the user the prepaid slow-mode fee and an additional 3-day wait before they can retry.

---

### Finding Description

`withdrawCollateral` applies two hard stops after debiting the balance:

```solidity
spotEngine.updateBalance(productId, sender, amountRealized);
spotEngine.assertUtilization(productId);          // totalDeposits >= totalBorrows
...
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [1](#0-0) 

`assertUtilization` reverts when the withdrawal would push total borrows above total deposits:

```solidity
require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
``` [2](#0-1) 

Both `WithdrawCollateral` and `WithdrawCollateralV2` carry only a fixed `uint128 amount` field — there is no sentinel (e.g., `type(uint128).max`) that the protocol would interpret as "use the maximum safe amount":

```solidity
struct WithdrawCollateral {
    bytes32 sender;
    uint32 productId;
    uint128 amount;   // no max sentinel
    uint64 nonce;
}
``` [3](#0-2) 

```solidity
struct WithdrawCollateralV2 {
    bytes32 sender;
    uint32 productId;
    uint128 amount;   // no max sentinel
    uint64 nonce;
    address sendTo;
    uint128 appendix; // Reserved for forward-compatible withdrawal features.
}
``` [4](#0-3) 

The slow-mode execution path silently swallows failures. The comment `// try return funds now removed` confirms that a prior refund-on-failure mechanism was deliberately deleted:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [5](#0-4) 

The slow-mode fee is charged **upfront** at submission time, before the 3-day delay elapses:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [6](#0-5) 

The slow-mode withdrawal handler in `processSlowModeTransactionImpl` passes `txn.amount` verbatim to `withdrawCollateral` with no clamping or max-resolution logic:

```solidity
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
``` [7](#0-6) 

---

### Impact Explanation

The slow-mode path is the **safety fallback** users rely on when the sequencer is unresponsive. In that exact scenario — a bank run or sequencer outage — pool utilization is likely near 100 % and leveraged subaccounts have health near zero. A user who submits a slow-mode `WithdrawCollateral` with their full balance:

1. Pays the slow-mode fee upfront (non-refundable).
2. Waits 3 days (`SLOW_MODE_TX_DELAY`).
3. The transaction is silently dropped because `assertUtilization` or `getHealth` reverts.
4. The user must resubmit (paying the fee again) and wait another 3 days.

Each failed attempt extends the effective fund lock by 3 days. Because there is no on-chain view function that returns the exact maximum safe withdrawal amount at a given block, the user cannot reliably compute the correct value off-chain either (state changes between submission and execution). The result is a compounding, user-initiated fund lock during the period when timely access to funds matters most.

---

### Likelihood Explanation

High. The slow-mode path is specifically designed for adversarial conditions (sequencer failure, bank runs). Those conditions are precisely when utilization is highest and health margins are thinnest. Any user with a leveraged spot position or a position in a high-utilization pool who attempts a full withdrawal via slow mode will trigger this path. No special privileges or external conditions beyond normal protocol use are required.

---

### Recommendation

**Short term:** Treat `amount == type(uint128).max` as a sentinel in `withdrawCollateral` (and its slow-mode dispatch in `processSlowModeTransactionImpl`) that resolves to the maximum amount satisfying both `assertUtilization` and `getHealth >= 0` before executing the transfer. Alternatively, expose a view function that returns the current maximum safe withdrawal amount for a given subaccount and product so users can query it before signing.

**Long term:** Add the `appendix` field already reserved in `WithdrawCollateralV2` as the canonical location for a max-withdrawal flag, and document the behavior clearly so integrators and front-ends can surface it to users.

---

### Proof of Concept

1. Alice holds 1 000 USDC in a spot product with 80 % utilization and a leveraged borrow that leaves her health at `+5`.
2. The sequencer goes offline. Alice submits a slow-mode `WithdrawCollateral` for `amount = 1000e6` (her full balance).
3. She pays the slow-mode fee upfront.
4. Three days later, `executeSlowModeTransaction` fires. `assertUtilization` reverts because the withdrawal would push utilization above 100 %. The `try/catch` silently drops the transaction.
5. Alice resubmits for `amount = 800e6`. Three days later, `getHealth` reverts because the withdrawal would bring her health below 0.
6. Alice resubmits for `amount = 400e6`. This succeeds — but she has now waited 9 days and paid three slow-mode fees to recover half her collateral, with the remainder still locked pending further guessing.

### Citations

**File:** core/contracts/Clearinghouse.sol (L412-419)
```text
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/SpotEngine.sol (L232-241)
```text
    function assertUtilization(uint32 productId) external view {
        (State memory _state, ) = getStateAndBalance(productId, X_ACCOUNT);
        int128 totalDeposits = _state.totalDepositsNormalized.mul(
            _state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = _state.totalBorrowsNormalized.mul(
            _state.cumulativeBorrowsMultiplierX18
        );
        require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L80-85)
```text
    struct WithdrawCollateral {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L97-104)
```text
    struct WithdrawCollateralV2 {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
        address sendTo;
        uint128 appendix; // Reserved for forward-compatible withdrawal features.
    }
```

**File:** core/contracts/Endpoint.sol (L205-228)
```text
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
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

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```
