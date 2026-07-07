### Title
Stale Slow-Mode `LinkSigner` Overrides Fast-Path Revocation, Re-Authorizing a Revoked Signer — (`core/contracts/EndpointTx.sol`)

---

### Summary

The Nado slow-mode queue has no mechanism to validate the *current* state of `linkedSigners` at execution time. A `LinkSigner` transaction submitted to the slow-mode queue and later superseded by a fast-path revocation will still execute after the 3-day delay, unconditionally re-enabling the revoked signer. Because there is no `cancelSlowModeTransaction` function, the user cannot prevent this.

---

### Finding Description

The protocol supports two paths for `LinkSigner`:

**Fast path** (sequencer-submitted, immediate): [1](#0-0) 

**Slow-mode path** (user-submitted, 3-day delay): [2](#0-1) 

When the slow-mode `LinkSigner` is executed via `processSlowModeTransactionImpl`, it performs only a `validateSender` check (confirming the original submitter) and then **unconditionally overwrites** `linkedSigners[txn.sender]`:

```solidity
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

There is no check on whether `linkedSigners[txn.sender]` has been updated since the slow-mode transaction was submitted. The `linkedSigners` mapping is the sole source of truth for signer authorization: [3](#0-2) 

The slow-mode queue is FIFO with no cancellation path. Once submitted, the transaction will execute: [4](#0-3) 

The 3-day delay is hardcoded at submission: [5](#0-4) 

---

### Impact Explanation

After the slow-mode `LinkSigner` executes and re-enables the revoked `Signer_X`, that signer can sign any `allowLinkedSigner = true` transaction on behalf of the victim subaccount. This includes:

- `WithdrawCollateral` / `WithdrawCollateralV2` — direct fund extraction
- `TransferQuote` — transfer quote token to an attacker-controlled subaccount
- `MintNlp` / `BurnNlp` — pool manipulation

The fast-path `WithdrawCollateral` path explicitly allows linked signers: [6](#0-5) 

**Broken invariant**: "A user's linked signer should reflect their most recent authorization decision." A fast-path revocation (`linkedSigners[sender] = address(0)`) is silently overridden by the stale slow-mode transaction, restoring access to a key the user explicitly revoked.

---

### Likelihood Explanation

The scenario is realistic in a censorship-resistance context:

1. The sequencer is temporarily unavailable or censoring the user.
2. User A submits a slow-mode `LinkSigner` to authorize `Signer_X` for automated trading.
3. `Signer_X`'s private key is later compromised, or the user changes their mind.
4. The sequencer is back online; User A submits a fast-path `LinkSigner` to revoke (`signer = address(0)`). The user believes they are safe.
5. After 3 days, the slow-mode transaction executes, re-enabling `Signer_X`.
6. The attacker (holder of `Signer_X`) signs a `WithdrawCollateral` and drains the account.

There is no `cancelSlowModeTransaction` function in `Endpoint.sol`, so the user has no recourse once the slow-mode transaction is in the queue. [7](#0-6) 

---

### Recommendation

In `processSlowModeTransactionImpl`, before setting the linked signer, validate that the current `linkedSigners[txn.sender]` has not been updated to a different value since the slow-mode transaction was submitted. One approach is to include the **expected current signer** in the `LinkSigner` struct and require it to match at execution time:

```solidity
// In processSlowModeTransactionImpl, LinkSigner branch:
require(
    linkedSigners[txn.sender] == txn.expectedCurrentSigner,
    "LinkedSigner state changed since submission"
);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

Alternatively, add a `cancelSlowModeTransaction` function that allows the original submitter to invalidate a pending slow-mode transaction before it executes.

---

### Proof of Concept

```
1. User A submits slow-mode LinkSigner { sender: subaccount_A, signer: Signer_X }
   → queued at slowModeTxs[N], executableAt = now + 3 days

2. Day 1: Signer_X key is compromised.

3. Day 1: User A submits fast-path LinkSigner { sender: subaccount_A, signer: address(0) }
   via sequencer → linkedSigners[subaccount_A] = address(0)
   User A believes Signer_X is revoked.

4. Day 3+: Anyone calls executeSlowModeTransaction()
   → processSlowModeTransactionImpl executes the queued LinkSigner
   → linkedSigners[subaccount_A] = Signer_X  ← revocation silently overridden

5. Attacker (holding Signer_X) signs WithdrawCollateral for full balance
   → validateSignedTx passes (allowLinkedSigner = true, Signer_X is now active)
   → clearinghouse.withdrawCollateral drains subaccount_A
```

### Citations

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L374-380)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```

**File:** core/contracts/Endpoint.sol (L185-229)
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
    }
```
