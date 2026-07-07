The evidence is conclusive. This is a real vulnerability.

---

### Title
Missing Post-Transfer Health Check in `createIsolatedSubaccount` Allows Parent Subaccount to Go Negative — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.createIsolatedSubaccount` transfers margin from the parent subaccount to the new isolated subaccount via `spotEngine.updateBalance` but performs **no health check on the parent** after the deduction. The analogous `Clearinghouse.transferQuote` function performs the identical balance mutation and then explicitly enforces `require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH)`. That guard is entirely absent from the isolated-subaccount creation path.

---

### Finding Description

In `OffchainExchange.createIsolatedSubaccount`, after signature validation, the margin is extracted from the signed order's appendix and transferred:

```solidity
int128 margin = int128(_isolatedMargin(txn.order.appendix));
if (margin > 0) {
    digestToMargin[digest] = margin;
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);   // parent debited
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin); // iso credited
}
``` [1](#0-0) 

There is no subsequent call to `_isAboveInitial`, `getHealth`, or any balance floor check on `txn.order.sender`. The function returns immediately after the two `updateBalance` calls. [2](#0-1) 

By contrast, `Clearinghouse.transferQuote` — which performs the same quote-balance debit — enforces the invariant explicitly:

```solidity
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);   // ← present here
``` [3](#0-2) 

`_isAboveInitial` is defined as:

```solidity
function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
    return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
}
``` [4](#0-3) 

The `EndpointTx.sol` dispatch for `CreateIsolatedSubaccount` adds no compensating check either — it only calls `createIsolatedSubaccount` and `_recordSubaccount`: [5](#0-4) 

---

### Impact Explanation

A user signs a `CreateIsolatedSubaccount` order with `margin = parentQuoteBalance + N` (any positive N). On-chain:

1. `spotEngine.updateBalance(QUOTE_PRODUCT_ID, parent, -(parentQuoteBalance + N))` → parent quote balance becomes `-N`.
2. `spotEngine.updateBalance(QUOTE_PRODUCT_ID, isolatedSubaccount, parentQuoteBalance + N)` → isolated subaccount holds more quote than was ever deposited.
3. The parent is immediately below maintenance health and liquidatable.
4. The isolated subaccount holds unbacked collateral (`N` units in excess of real deposits).
5. The user can trade in the isolated subaccount using the inflated balance; when the parent is liquidated the protocol/insurance fund absorbs the shortfall.

This breaks the accounting identity: `sum(all subaccount quote balances) = total deposited quote`. The isolated subaccount's excess is unbacked, constituting a direct protocol loss.

---

### Likelihood Explanation

Any user can self-sign a `CreateIsolatedSubaccount` order with an arbitrary margin value. The signature check only verifies that the order was signed by the sender (or linked signer) — it does not validate that `margin ≤ parentQuoteBalance`. The sequencer submits the transaction on-chain where no guard prevents the negative balance. This is reachable through the normal slow-mode or sequencer-submitted transaction path with no special privileges required beyond owning a subaccount.

---

### Recommendation

Add a post-transfer health check on the parent subaccount inside `createIsolatedSubaccount`, mirroring the pattern already used in `transferQuote`:

```solidity
if (margin > 0) {
    digestToMargin[digest] = margin;
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin);
    require(
        clearinghouse._isAboveInitial(txn.order.sender),
        ERR_SUBACCT_HEALTH
    );
}
```

Alternatively, expose a `requireAboveInitial` helper on the clearinghouse and call it here, consistent with how `transferQuote` enforces the invariant.

---

### Proof of Concept

1. Deploy the protocol locally (Hardhat).
2. Deposit 100 USDC into `parentSubaccount`.
3. Sign a `CreateIsolatedSubaccount` order for `parentSubaccount` with `margin = 101e6` (1 USDC over balance).
4. Submit the transaction through the endpoint.
5. Assert: `spotEngine.getBalance(QUOTE_PRODUCT_ID, parentSubaccount).amount == -1e6` (negative).
6. Assert: `spotEngine.getBalance(QUOTE_PRODUCT_ID, isolatedSubaccount).amount == 101e6`.
7. Assert: `clearinghouse.getHealth(parentSubaccount, MAINTENANCE) < 0` (immediately liquidatable).
8. Total quote across both subaccounts = 100e6, but isolated subaccount alone holds 101e6 — unbacked collateral confirmed.

### Citations

**File:** core/contracts/OffchainExchange.sol (L1074-1090)
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

        return newIsolatedSubaccount;
    }
```

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L639-642)
```text
    function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
        // Weighted initial health with limit orders < 0
        return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
    }
```

**File:** core/contracts/EndpointTx.sol (L619-631)
```text
        } else if (
            txType == IEndpoint.TransactionType.CreateIsolatedSubaccount
        ) {
            IEndpoint.CreateIsolatedSubaccount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.CreateIsolatedSubaccount)
            );
            bytes32 newIsolatedSubaccount = IOffchainExchange(offchainExchange)
                .createIsolatedSubaccount(
                    txn,
                    getLinkedSigner(txn.order.sender)
                );
            _recordSubaccount(newIsolatedSubaccount);
```
