### Title
Fast Withdrawal Fees Permanently Locked in `BaseWithdrawPool` With No Distribution Mechanism - (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool` accumulates real ERC20 tokens as fast-withdrawal fees into the `fees` mapping, but neither `BaseWithdrawPool` nor its concrete implementation `WithdrawPool` contains any function to withdraw or distribute those fees to protocol beneficiaries (NLP stakers, treasury, etc.). The fees are permanently locked until a contract upgrade.

---

### Finding Description

Every call to `submitFastWithdrawal` charges a fee and records it:

```solidity
// BaseWithdrawPool.sol line 111
fees[productId] += fee;
```

When `sendTo == msg.sender`, the fee is deducted from the transferred amount (the contract retains the extra tokens). When `sendTo != msg.sender`, the fee is pulled from `msg.sender` via `safeTransferFrom` directly into the contract. Either way, real ERC20 tokens accumulate in the contract's balance, tracked by `fees[productId]`. [1](#0-0) [2](#0-1) 

The entire `BaseWithdrawPool` contract has only one owner-callable function that moves tokens out — `removeLiquidity`:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo)
    external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [3](#0-2) 

`removeLiquidity` does not reference the `fees` mapping at all — it is a general liquidity-removal escape hatch, not a fee-distribution function. The `fees` mapping is **never decremented** anywhere in the codebase.

`WithdrawPool.sol` is a thin wrapper that adds only `initialize()` and introduces no fee-withdrawal logic: [4](#0-3) 

---

### Impact Explanation

Fast-withdrawal fees are real ERC20 tokens (USDC or other collateral assets) that belong to the protocol and should flow to NLP stakers or the treasury. Because no withdrawal path exists, every fee collected via `submitFastWithdrawal` is permanently locked in the `WithdrawPool` contract. The `fees` mapping grows monotonically but is never consumed. The only escape is a contract upgrade, which is not a normal operational path.

---

### Likelihood Explanation

`submitFastWithdrawal` is a public, permissionless function callable by any liquidity provider who wants to front a user's withdrawal. It is a core operational feature of the protocol. Fees accumulate on every such call, so the locked-funds balance grows continuously with normal protocol usage. No special attacker action is required — the loss is automatic. [5](#0-4) 

---

### Recommendation

Add a privileged `withdrawFees(uint32 productId, address recipient)` function to `BaseWithdrawPool` that reads `fees[productId]`, resets it to zero, and transfers the corresponding ERC20 amount to the designated recipient (protocol treasury or NLP pool subaccount). This mirrors the pattern used by `dumpFees()` in `OffchainExchange` and `claimSequencerFees()` in `Clearinghouse`. [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. Alice calls `submitFastWithdrawal(idx, transaction, signatures)` fronting a 10,000 USDC withdrawal.
2. `fastWithdrawalFeeAmount` returns, say, 10 USDC.
3. Since `sendTo == msg.sender`, `transferAmount` is reduced by 10 USDC; the contract retains 10 USDC extra.
4. `fees[productId] += 10e6` is recorded.
5. Repeat for thousands of fast withdrawals — `fees[productId]` grows to millions of USDC.
6. No function exists to send those tokens to NLP stakers or the treasury.
7. The `fees` mapping is a permanently increasing counter with no corresponding debit path. [8](#0-7)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L39-42)
```text
    // collected withdrawal fees in native token decimals
    mapping(uint32 => int128) public fees;

    uint64 public minIdx;
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/WithdrawPool.sol (L15-19)
```text
contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
}
```

**File:** core/contracts/OffchainExchange.sol (L891-930)
```text
    function dumpFees() external onlyEndpoint {
        // loop over all spot and perp product ids
        uint32[] memory productIds = spotEngine.getProductIds();

        for (uint32 i = 1; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            spotEngine.updateBalance(
                quoteIds[productId],
                X_ACCOUNT,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }

        productIds = perpEngine.getProductIds();

        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            perpEngine.updateBalance(
                productId,
                X_ACCOUNT,
                0,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }
```

**File:** core/contracts/Clearinghouse.sol (L569-615)
```text
    function claimSequencerFees(int128[] calldata fees)
        external
        virtual
        onlyEndpoint
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        uint32[] memory spotIds = spotEngine.getProductIds();
        uint32[] memory perpIds = perpEngine.getProductIds();

        for (uint256 i = 0; i < spotIds.length; i++) {
            ISpotEngine.Balance memory feeBalance = spotEngine.getBalance(
                spotIds[i],
                FEES_ACCOUNT
            );
            spotEngine.updateBalance(
                spotIds[i],
                X_ACCOUNT,
                fees[i] + feeBalance.amount
            );
            spotEngine.updateBalance(
                spotIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount
            );
        }

        for (uint256 i = 0; i < perpIds.length; i++) {
            IPerpEngine.Balance memory feeBalance = perpEngine.getBalance(
                perpIds[i],
                FEES_ACCOUNT
            );
            perpEngine.updateBalance(
                perpIds[i],
                X_ACCOUNT,
                feeBalance.amount,
                feeBalance.vQuoteBalance
            );
            perpEngine.updateBalance(
                perpIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount,
                -feeBalance.vQuoteBalance
            );
        }
    }
```
