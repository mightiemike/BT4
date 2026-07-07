### Title
Slow Mode Fees Permanently Locked in Endpoint Contract with No Claim Mechanism — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `slowModeFees` variable in `EndpointStorage` accumulates quote tokens transferred from users when they submit slow mode transactions, but no function exists anywhere in the protocol to claim or withdraw these accumulated fees. The tokens are permanently locked in the Endpoint contract.

---

### Finding Description

When a user submits a slow mode transaction via `submitSlowModeTransactionImpl`, the protocol charges a slow mode fee:

```solidity
// EndpointTx.sol lines 370-371
chargeSlowModeFee(_getQuote(), sender);
slowModeFees += SLOW_MODE_FEE;
```

`chargeSlowModeFee` (defined in `EndpointStorage.sol`) transfers quote tokens from the sender directly into the Endpoint contract (`address(this)`):

```solidity
// EndpointStorage.sol lines 83-93
function chargeSlowModeFee(IERC20Base token, address from) internal virtual {
    require(address(token) != address(0));
    token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
}
```

The accumulated amount is tracked in `slowModeFees` (declared at `EndpointStorage.sol` line 55). However, unlike `sequencerFee` — which is claimable via the `DumpFees` slow mode transaction that calls `clearinghouse.claimSequencerFees(fees)` — there is no corresponding mechanism to claim or withdraw the `slowModeFees` balance. A full audit of `Endpoint.sol`, `EndpointTx.sol`, and `EndpointStorage.sol` confirms no function reads or drains `slowModeFees`.

The `DumpFees` handler in `processSlowModeTransactionImpl` explicitly handles `sequencerFee` per product but makes no reference to `slowModeFees`:

```solidity
// EndpointTx.sol lines 244-253
} else if (txType == IEndpoint.TransactionType.DumpFees) {
    IOffchainExchange(offchainExchange).dumpFees();
    uint32[] memory spotIds = spotEngine.getProductIds();
    int128[] memory fees = new int128[](spotIds.length);
    for (uint256 i = 0; i < spotIds.length; i++) {
        fees[i] = sequencerFee[spotIds[i]];
        sequencerFee[spotIds[i]] = 0;
    }
    ...
    clearinghouse.claimSequencerFees(fees);
```

`slowModeFees` is never zeroed, never transferred out, and never read by any external or internal function.

---

### Impact Explanation

Every non-owner slow mode transaction (e.g., `WithdrawCollateral`, `LinkSigner`, `ClaimBuilderFee`) charges a slow mode fee in the quote token (USDC). These tokens are transferred to the Endpoint contract and tracked in `slowModeFees`, but are permanently unrecoverable. The protocol loses all slow mode fee revenue. The amount grows monotonically with protocol usage and is never accessible to any party.

---

### Likelihood Explanation

High. Every user-initiated slow mode transaction triggers this code path. Slow mode transactions are a standard fallback mechanism for withdrawals and other user actions. The accumulation is continuous and irreversible under the current code.

---

### Recommendation

Implement a claim function for `slowModeFees`, analogous to how `sequencerFee` is claimed via `DumpFees`. The simplest fix is to include `slowModeFees` in the `DumpFees` handler: transfer the accumulated `slowModeFees` balance from the Endpoint contract to the clearinghouse or `X_ACCOUNT`, then reset `slowModeFees = 0`. Alternatively, add a dedicated owner-callable function to withdraw the accumulated slow mode fees.

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral` payload.
2. `submitSlowModeTransactionImpl` reaches the `else` branch at line 369 (non-owner tx type), calls `chargeSlowModeFee(_getQuote(), sender)`.
3. `chargeSlowModeFee` executes `token.safeTransferFrom(sender, address(this), clearinghouse.getSlowModeFee())` — quote tokens now sit in the Endpoint contract.
4. `slowModeFees += SLOW_MODE_FEE` is executed — the accounting variable is updated.
5. No function in `Endpoint.sol`, `EndpointTx.sol`, or any other contract reads or drains `slowModeFees`.
6. Tokens are permanently locked. Repeat for every slow mode submission; the locked balance grows unboundedly. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/EndpointTx.sol (L244-253)
```text
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
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
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
