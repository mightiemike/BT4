### Title
Read-Only Reentrancy in `Clearinghouse.withdrawCollateral` — Subaccount Balance Readable as Pre-Withdrawal During Token Transfer Callback - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` transfers tokens to the recipient **before** updating the subaccount balance in `SpotEngine`. No `nonReentrant` guard exists anywhere in the protocol. During the token transfer callback, any external contract reading `SpotEngine` or `Clearinghouse` state observes a transiently inflated subaccount balance — the classic read-only reentrancy pattern.

---

### Finding Description

`Clearinghouse.withdrawCollateral` executes in this order:

```
1. handleWithdrawTransfer(token, sendTo, amount, idx)   ← external call, tokens reach sendTo
2. spotEngine.updateBalance(productId, sender, amountRealized)  ← state update AFTER
3. spotEngine.assertUtilization(productId)
4. require(getHealth(sender, healthType) >= 0, ...)
``` [1](#0-0) 

`handleWithdrawTransfer` in `Clearinghouse` first sends tokens to `withdrawPool`, then calls `withdrawPool.submitWithdrawal`, which calls `token.safeTransfer(sendTo, amount)`: [2](#0-1) [3](#0-2) 

At the moment `token.safeTransfer(sendTo, amount)` executes, `spotEngine.updateBalance` has **not yet been called**. The mapping `balances[productId][sender]` in `SpotEngineState` still holds the full pre-withdrawal normalized amount: [4](#0-3) 

A grep across all production contracts confirms **zero uses** of `nonReentrant` or `ReentrancyGuard` anywhere in the codebase. Neither `withdrawCollateral`, `Endpoint.executeSlowModeTransaction`, nor `Endpoint.submitTransactionsChecked` carry any reentrancy protection.

---

### Impact Explanation

During the token transfer callback to `sendTo`, any external contract can call:

- `SpotEngine.getBalance(productId, sender)` — returns the pre-withdrawal (inflated) balance
- `Clearinghouse.getHealth(sender, healthType)` — returns inflated health, because it calls `spotEngine.getHealthContribution` which reads the same stale `balances` mapping [5](#0-4) 

Any external lending protocol, vault, or integration that reads subaccount collateral or health from Nado during this window observes a balance that is `amount` tokens higher than the true post-withdrawal value. This can be used to borrow against inflated collateral in an external protocol that trusts Nado's on-chain state as a price/collateral oracle.

The corrupted state is: `SpotEngine.balances[productId][sender].amountNormalized` — it remains at the pre-withdrawal level for the entire duration of the external call chain, which includes at minimum one ERC-20 `transfer` callback.

---

### Likelihood Explanation

Two unprivileged entry paths exist:

**Path 1 — Slow mode (fully permissionless):** `Endpoint.executeSlowModeTransaction` has no access control. Any user can call it to process a queued `WithdrawCollateral` slow mode transaction. If the user's wallet is a contract (e.g., a smart contract wallet, a Gnosis Safe, or a purpose-built attacker contract), the ERC-20 transfer to `sendTo` triggers a `receive()` or ERC-777/hook callback. [6](#0-5) [7](#0-6) 

**Path 2 — `WithdrawCollateralV2` via sequencer:** The `sendTo` field is an explicit address that can be set to any contract. The sequencer processes this and calls `clearinghouse.withdrawCollateral(..., signedTx.tx.sendTo, ...)`, transferring tokens to the attacker-controlled contract before the balance update. [8](#0-7) 

Smart contract wallets are common on EVM chains. The Ink ecosystem, being EVM-compatible, supports them natively. The likelihood of a contract address being a withdrawal recipient is realistic.

---

### Recommendation

Apply the Checks-Effects-Interactions pattern: move `spotEngine.updateBalance` and `spotEngine.assertUtilization` **before** `handleWithdrawTransfer`. The health check at line 419 must also remain after the balance update but can be placed before the transfer since it only reads state.

Alternatively, add OpenZeppelin's `ReentrancyGuard` and apply `nonReentrant` to `withdrawCollateral`, `executeSlowModeTransaction`, and `submitFastWithdrawal`. However, CEI reordering is the cleaner fix and eliminates the read-only reentrancy vector entirely, since the state will be consistent before any external call is made.

---

### Proof of Concept

```
1. Attacker deploys MaliciousRecipient contract that, on ERC-20 receipt, calls
   SpotEngine.getBalance(productId, victimSubaccount) and records the result.

2. Attacker registers a subaccount with address = MaliciousRecipient.

3. Attacker deposits collateral and submits a WithdrawCollateral slow mode tx
   (or uses WithdrawCollateralV2 with sendTo = MaliciousRecipient).

4. After the slow mode delay, attacker calls Endpoint.executeSlowModeTransaction().

5. Execution flow:
   Endpoint.executeSlowModeTransaction()
     → Clearinghouse.withdrawCollateral(sender, productId, amount, MaliciousRecipient, idx)
       → handleWithdrawTransfer(token, MaliciousRecipient, amount, idx)
         → token.safeTransfer(withdrawPool, amount)
         → withdrawPool.submitWithdrawal(token, MaliciousRecipient, amount, idx)
           → token.safeTransfer(MaliciousRecipient, amount)
             *** ERC-20 transfer callback fires ***
             → MaliciousRecipient.onTokenReceived() calls:
               SpotEngine.getBalance(productId, sender)
               // Returns pre-withdrawal balance (amount not yet deducted)
               Clearinghouse.getHealth(sender, INITIAL)
               // Returns inflated health
             *** callback returns ***
         → (returns to Clearinghouse)
       → spotEngine.updateBalance(productId, sender, -amountRealized)  ← too late
       → spotEngine.assertUtilization(productId)
       → require(getHealth(sender, INITIAL) >= 0)

6. Any external protocol that trusted the balance/health read in step 5
   has been shown inflated collateral for the attacker's subaccount.
``` [9](#0-8)

### Citations

**File:** core/contracts/Clearinghouse.sol (L71-84)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);
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

**File:** core/contracts/SpotEngineState.sol (L11-13)
```text
    mapping(uint32 => State) internal states;
    mapping(uint32 => mapping(bytes32 => BalanceNormalized)) internal balances;
    mapping(bytes32 => NlpLockedBalanceQueue) internal nlpLockedBalanceQueues;
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
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
