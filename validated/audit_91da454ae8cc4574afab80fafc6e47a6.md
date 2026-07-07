### Title
Sanctioned Users Cannot Recover Deposited Collateral via Any User-Controlled On-Chain Path — (`core/contracts/EndpointTx.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

A user who deposits collateral and is subsequently added to the on-chain sanctions list loses access to their funds permanently. The only user-controlled on-chain withdrawal path (slow mode) is gated by `requireUnsanctioned` at **submission time**, not at deposit time. Once sanctioned, the user cannot submit a slow mode withdrawal, and no admin recovery mechanism exists to return their funds.

---

### Finding Description

**Deposit path** (`Endpoint.sol::depositCollateralWithReferral`):

At deposit time, `requireUnsanctioned` is checked and funds are taken from the user via `handleDepositTransfer`. A `SlowModeTx` is queued with a 3-day delay. [1](#0-0) 

**Slow mode withdrawal submission** (`EndpointTx.sol::submitSlowModeTransactionImpl`):

When a user later tries to submit a slow mode `WithdrawCollateral` transaction, `requireUnsanctioned(sender)` is enforced at line 375 — **after** the deposit has already been credited. If the user has been sanctioned in the interim, this call reverts and the withdrawal cannot be queued. [2](#0-1) 

**Sequencer-processed withdrawal path** (`EndpointTx.sol::processTransactionImpl`):

The sequencer-submitted `WithdrawCollateral` path has **no on-chain sanctions check**: [3](#0-2) 

However, the sequencer is a centralized off-chain component. A sanctioned user cannot compel the sequencer to include their withdrawal. This is not a guaranteed on-chain recovery path.

**No admin recovery function exists.** There is no function in `Clearinghouse.sol` or `Endpoint.sol` analogous to the `revokePayout` fix — no mechanism for an admin to return a sanctioned user's collateral to any address.

**Silent failure comment confirms the gap:**

The comment `// try return funds now removed` in `_executeSlowModeTransaction` confirms that a prior fund-return mechanism was deliberately removed, leaving no fallback for failed slow mode transactions. [4](#0-3) 

---

### Impact Explanation

A user who deposits collateral and is subsequently added to the Chainalysis sanctions list has their funds permanently locked on-chain:

- The slow mode withdrawal path (the only user-controlled, sequencer-independent on-chain path) is blocked by `requireUnsanctioned` at submission time.
- The sequencer path has no on-chain sanctions check but is not user-controlled.
- No admin function exists to recover or redirect the locked collateral.

The corrupted asset delta is: the user's spot balance in `SpotEngine` remains positive but is unreachable — the user cannot debit it via any guaranteed on-chain path.

---

### Likelihood Explanation

Any user depositing collateral who is subsequently added to the OFAC/Chainalysis sanctions list is affected. This is a realistic, non-theoretical event (e.g., a user's wallet is flagged after a mixer interaction). The protocol already integrates a live sanctions oracle, confirming this scenario is within the threat model.

---

### Recommendation

Add an admin-restricted recovery function (analogous to the `revokePayout` fix in PR 36) that allows the owner to withdraw a sanctioned user's collateral to a designated compliance address (e.g., a government-controlled or protocol-controlled escrow). This mirrors the exact fix applied to the PayoutManager analog:

```solidity
function revokeCollateral(
    bytes32 subaccount,
    uint32 productId,
    uint128 amount,
    address complianceRecipient
) external onlyOwner {
    // transfer balance from subaccount to complianceRecipient
}
```

---

### Proof of Concept

1. User calls `Endpoint::depositCollateral` — `requireUnsanctioned` passes, funds are taken, slow mode deposit tx is queued. [5](#0-4) 

2. User's address is added to the on-chain sanctions list (external event, within protocol's threat model).

3. User calls `Endpoint::submitSlowModeTransaction` with a `WithdrawCollateral` payload.

4. Inside `EndpointTx::submitSlowModeTransactionImpl`, execution reaches `requireUnsanctioned(sender)` at line 375 and **reverts**. [6](#0-5) 

5. The user's spot balance in `SpotEngine` remains positive but no on-chain path exists to debit it. The sequencer path (`processTransactionImpl`) has no on-chain sanctions check but is not user-controlled. [3](#0-2) 

6. No admin recovery function exists in `Clearinghouse.sol` or `Endpoint.sol` to return the funds. [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
```

**File:** core/contracts/Endpoint.sol (L133-148)
```text
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

**File:** core/contracts/EndpointTx.sol (L369-385)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
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
