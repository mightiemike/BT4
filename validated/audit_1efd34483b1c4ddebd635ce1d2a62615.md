### Title
Missing Expiration Check in `createIsolatedSubaccount` Allows Margin Lock with No User-Accessible Recovery - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`createIsolatedSubaccount` in `OffchainExchange.sol` transfers margin from a parent subaccount into a newly created isolated subaccount without verifying that the embedded order's `expiration` field is in the future. If the order is already expired at creation time, the margin is locked in the isolated subaccount and the order can never be matched. The only recovery path (`tryCloseIsolatedSubaccount`) is not accessible to the user via the slow-mode queue — it requires sequencer cooperation.

---

### Finding Description

`createIsolatedSubaccount` validates the order's signature and isolated-flag bits, but performs no check that `txn.order.expiration > getOracleTime()`: [1](#0-0) 

After passing those checks, it unconditionally transfers margin: [2](#0-1) 

By contrast, `_validateOrder` — the function that guards every `matchOrders` call — explicitly rejects expired orders: [3](#0-2) [4](#0-3) 

So the lifecycle is broken: margin is moved at creation time without the expiration guard that is enforced at match time.

The only way to recover the margin is via `tryCloseIsolatedSubaccount`, which is callable only by the endpoint or clearinghouse: [5](#0-4) 

This function is dispatched exclusively through `processTransactionImpl` (sequencer path): [6](#0-5) 

It is **absent** from `processSlowModeTransactionImpl`, meaning a user cannot self-recover via the slow-mode queue: [7](#0-6) 

---

### Impact Explanation

A user whose isolated order is created with an already-expired `expiration` (due to a UI bug, clock skew, or a simple mistake) will have their margin balance debited from their parent subaccount and credited to the isolated subaccount. Because `_validateOrder` will always reject the expired order, `matchOrders` can never execute against it. The margin sits in the isolated subaccount indefinitely. Recovery requires the sequencer to submit a `CloseIsolatedSubaccount` transaction; if the sequencer does not do so (bug, delay, or censorship), the user has no on-chain self-help path. The `SLOW_MODE_TX_DELAY` is 3 days, but `CloseIsolatedSubaccount` is not even a valid slow-mode transaction type. [8](#0-7) 

---

### Likelihood Explanation

The `expiration` field is a `uint64` set by the user (or their UI) at order-signing time. Clock skew between the user's device and the sequencer's oracle time, a stale cached order replayed after its expiration, or a UI that pre-populates an incorrect timestamp are all realistic triggers. The PoolTogether report was elevated to High precisely because a UI bug or simple mistake was sufficient — the same reasoning applies here.

---

### Recommendation

Add an expiration guard at the top of `createIsolatedSubaccount`, mirroring the check already present in `_validateOrder`:

```solidity
function createIsolatedSubaccount(
    IEndpoint.CreateIsolatedSubaccount memory txn,
    address linkedSigner
) external onlyEndpoint returns (bytes32) {
    require(!_expired(txn.order.expiration), "order expired");
    require(
        !RiskHelper.isIsolatedSubaccount(txn.order.sender),
        ERR_UNAUTHORIZED
    );
    // ... rest of function
}
```

Additionally, consider adding `CloseIsolatedSubaccount` as a valid slow-mode transaction type so users retain a censorship-resistant recovery path.

---

### Proof of Concept

1. User signs a `CreateIsolatedSubaccount` order with `expiration = getOracleTime() - 1` (already expired), setting `_isolatedMargin` to some non-zero value in the `appendix`.
2. Sequencer submits the transaction via `submitTransactionsChecked` → `processTransactionImpl` → `createIsolatedSubaccount`.
3. `createIsolatedSubaccount` passes all checks (signature valid, isolated flag set, no expiration check). Margin `M` is deducted from the parent subaccount and credited to the new isolated subaccount.
4. Any subsequent `matchOrders` call referencing this order's digest hits `_validateOrder` → `_expired(order.expiration)` returns `true` → `require(..., ERR_INVALID_TAKER/MAKER)` reverts. The order can never fill.
5. User attempts to recover via `submitSlowModeTransaction` with a `CloseIsolatedSubaccount` payload. `submitSlowModeTransactionImpl` reaches the final `else { revert(); }` branch because `CloseIsolatedSubaccount` is not a handled slow-mode type. Recovery fails.
6. Margin `M` remains locked in the isolated subaccount until the sequencer voluntarily submits a `CloseIsolatedSubaccount` transaction.

### Citations

**File:** core/contracts/OffchainExchange.sol (L152-158)
```text
    function tryCloseIsolatedSubaccount(bytes32 subaccount) external virtual {
        require(
            msg.sender == getEndpoint() || msg.sender == address(clearinghouse),
            ERR_UNAUTHORIZED
        );
        _tryCloseIsolatedSubaccount(subaccount);
    }
```

**File:** core/contracts/OffchainExchange.sol (L345-347)
```text
    function _expired(uint64 expiration) internal view returns (bool) {
        return expiration <= getOracleTime();
    }
```

**File:** core/contracts/OffchainExchange.sol (L457-468)
```text
        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
            // valid amount
            (order.amount != 0) &&
            !_expired(order.expiration);
```

**File:** core/contracts/OffchainExchange.sol (L999-1020)
```text
    function createIsolatedSubaccount(
        IEndpoint.CreateIsolatedSubaccount memory txn,
        address linkedSigner
    ) external onlyEndpoint returns (bytes32) {
        require(
            !RiskHelper.isIsolatedSubaccount(txn.order.sender),
            ERR_UNAUTHORIZED
        );
        require(_isIsolated(txn.order.appendix), ERR_UNAUTHORIZED);
        bytes32 digest = getDigest(txn.productId, txn.order);
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
        require(
            _checkSignature(
                txn.order.sender,
                digest,
                linkedSigner,
                txn.signature
            ),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/OffchainExchange.sol (L1074-1087)
```text
        int128 margin = int128(_isolatedMargin(txn.order.appendix));
        if (margin > 0) {
            digestToMargin[digest] = margin;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.order.sender,
                -margin
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                newIsolatedSubaccount,
                margin
            );
        }
```

**File:** core/contracts/EndpointTx.sol (L202-330)
```text
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
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
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            clearinghouse.depositInsurance(transaction);
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
        } else if (txType == IEndpoint.TransactionType.WithdrawInsurance) {
            clearinghouse.withdrawInsurance(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.DelistProduct) {
            clearinghouse.delistProduct(transaction);
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
        } else if (txType == IEndpoint.TransactionType.RebalanceXWithdraw) {
            clearinghouse.rebalanceXWithdraw(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.UpdateTierFeeRates) {
            IEndpoint.UpdateTierFeeRates memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateTierFeeRates)
            );
            IOffchainExchange(offchainExchange).updateTierFeeRates(txn);
        } else if (txType == IEndpoint.TransactionType.AddNlpPool) {
            IEndpoint.AddNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.AddNlpPool)
            );
            addNlpPool(txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.UpdateNlpPool) {
            IEndpoint.UpdateNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateNlpPool)
            );
            updateNlpPool(txn.poolId, txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.DeleteNlpPool) {
            IEndpoint.DeleteNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DeleteNlpPool)
            );
            deleteNlpPool(txn.poolId);
        } else if (txType == IEndpoint.TransactionType.ForceRebalanceNlpPool) {
            IEndpoint.ForceRebalanceNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ForceRebalanceNlpPool)
            );
            clearinghouse.forceRebalanceNlpPool(
                nlpPools,
                txn.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
            IEndpoint.NlpProfitShare memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.NlpProfitShare)
            );
            require(
                txn.poolId > 0 && txn.poolId < nlpPools.length,
                ERR_INVALID_NLP_POOL
            );
            require(
                nlpPools[txn.poolId].owner != address(0),
                ERR_INVALID_NLP_POOL
            );
            require(
                address(uint160(bytes20(txn.recipient))) ==
                    nlpPools[txn.poolId].owner,
                ERR_UNAUTHORIZED
            );
            requireSubaccount(txn.recipient);
            require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
            clearinghouse.nlpProfitShare(
                nlpPools[txn.poolId].subaccount,
                txn.recipient,
                txn.amount
            );
        } else if (txType == IEndpoint.TransactionType.UpdateBuilder) {
            IOffchainExchange(offchainExchange).updateBuilder(transaction);
        } else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
            IEndpoint.ClaimBuilderFee memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ClaimBuilderFee)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            IOffchainExchange(offchainExchange).claimBuilderFee(
                txn.sender,
                txn.builderId
            );
        } else {
            revert();
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L633-641)
```text
            txType == IEndpoint.TransactionType.CloseIsolatedSubaccount
        ) {
            IEndpoint.CloseIsolatedSubaccount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.CloseIsolatedSubaccount)
            );
            IOffchainExchange(offchainExchange).tryCloseIsolatedSubaccount(
                txn.subaccount
            );
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
