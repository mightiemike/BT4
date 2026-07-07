### Title
Silent Failure of Slow-Mode Deposit Credit Permanently Locks User Tokens — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` transfers user tokens to the clearinghouse immediately (push pattern), then queues a `DepositCollateral` slow-mode transaction to credit the user's on-chain balance. If that slow-mode transaction reverts during execution, the catch block in `_executeSlowModeTransaction` silently discards the error and no refund is issued. A comment in the catch block explicitly reads `// try return funds now removed`, confirming the refund mechanism was deliberately deleted. Tokens are permanently locked in the clearinghouse with no credit and no recovery path.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` follows a two-step push pattern:

**Step 1 — Immediate token transfer (Endpoint.sol:144–148):**
`handleDepositTransfer` pulls tokens from `msg.sender` into the endpoint and forwards them to the clearinghouse atomically. [1](#0-0) 

`handleDepositTransfer` in `EndpointStorage.sol` performs `safeTransferFrom(token, from, amount)` then `safeTransferTo(token, address(clearinghouse), amount)`: [2](#0-1) 

**Step 2 — Deferred balance credit (Endpoint.sol:152–166):**
A `DepositCollateral` slow-mode transaction is queued. The actual on-chain balance credit (`spotEngine.updateBalance`) only happens when this slow-mode tx is later processed. [3](#0-2) 

**Silent failure in `_executeSlowModeTransaction` (Endpoint.sol:207–227):**
When the slow-mode tx is executed, a `try/catch` wraps `processSlowModeTransaction`. If it reverts, the catch block does nothing. The comment `// try return funds now removed` explicitly confirms a refund mechanism was removed: [4](#0-3) 

**Revert condition in `clearinghouse.depositCollateral` (Clearinghouse.sol:199):**
The clearinghouse enforces `require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW)`. Since `amount` is `uint128` (range `0` to `2^128-1`) and `INT128_MAX = 2^127-1`, any deposit with `amount > INT128_MAX` passes `isValidDepositAmount` (which only checks the minimum), takes the user's tokens, queues the slow-mode tx, and then silently fails the credit step: [5](#0-4) 

`INT128_MAX` is defined as `uint128(type(int128).max)`: [6](#0-5) 

The `isValidDepositAmount` check only enforces a minimum, not a maximum: [7](#0-6) 

---

### Impact Explanation

User tokens are transferred to the clearinghouse contract and permanently locked. No on-chain balance credit is issued. No refund path exists — the comment `// try return funds now removed` confirms this was explicitly removed. The tokens accumulate in the clearinghouse and are effectively socialized into the protocol's collateral pool, benefiting all existing depositors at the expense of the affected user. There is no on-chain mechanism for the user to recover their funds.

---

### Likelihood Explanation

The `amount > INT128_MAX` trigger requires depositing more than `2^127-1` raw token units. For tokens with 6 decimals (e.g., USDC), this is astronomically large and practically unreachable. However, the structural vulnerability is unconditional: **any** future revert condition introduced in `clearinghouse.depositCollateral` — a new validation, a paused state, a token decimals check (`require(decimals <= MAX_DECIMALS)` for a newly listed token with decimals > 18) — would silently lock all in-flight deposits. The explicit removal of the refund mechanism (`// try return funds now removed`) makes this a latent, permanently open risk. Likelihood for the overflow path alone is low; likelihood of the structural issue manifesting via any future code change is medium.

---

### Recommendation

Restore a pull-pattern or refund mechanism for failed `DepositCollateral` slow-mode transactions. Options:

1. **Pull pattern:** Do not transfer tokens in `depositCollateralWithReferral`. Instead, record a pending deposit and transfer tokens only when the slow-mode tx is successfully processed.
2. **Refund on failure:** In the catch block of `_executeSlowModeTransaction`, detect `DepositCollateral` tx type and return the tokens to the original sender.
3. **Pre-validate:** Replicate all revert conditions from `clearinghouse.depositCollateral` inside `depositCollateralWithReferral` before taking custody of tokens, so the transaction reverts atomically rather than silently failing later.

---

### Proof of Concept

1. User calls `depositCollateralWithReferral(subaccount, productId, amount, referralCode)` where `amount = INT128_MAX + 1` (i.e., `2^127`).
2. `isValidDepositAmount` passes (only checks minimum deposit value).
3. `handleDepositTransfer` transfers `amount` tokens from user → endpoint → clearinghouse. Tokens are now in the clearinghouse.
4. A `DepositCollateral` slow-mode tx is queued with `amount = 2^127`.
5. After `SLOW_MODE_TX_DELAY` (3 days), anyone calls `executeSlowModeTransaction()`.
6. `_executeSlowModeTransaction` calls `try this.processSlowModeTransaction(...)`.
7. Inside, `clearinghouse.depositCollateral` hits `require(txn.amount <= INT128_MAX)` → reverts with `ERR_CONVERSION_OVERFLOW`.
8. The catch block at `Endpoint.sol:207` silently discards the revert (`// try return funds now removed`).
9. User's `2^127` raw token units remain in the clearinghouse. No balance credit. No refund. Tokens are permanently locked. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L90-101)
```text
    function isValidDepositAmount(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

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

**File:** core/contracts/Endpoint.sol (L185-228)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
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

**File:** core/contracts/common/Constants.sol (L30-30)
```text
uint128 constant INT128_MAX = uint128(type(int128).max);
```
