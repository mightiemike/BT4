### Title
Smart Contract Subaccount Can Tokenize Locked NLP Position, Bypassing the 4-Day Lock Period — (File: `core/contracts/Endpoint.sol`, `core/contracts/SpotEngine.sol`)

---

### Summary

There is no restriction on whether the address that creates a subaccount and mints NLP is an EOA or a smart contract. A malicious actor can deploy a wrapper smart contract that creates a Nado subaccount, mints NLP (which is subject to a 4-day lock period), and then tokenizes the wrapper contract itself — issuing ERC-20 shares that represent pro-rata ownership of the locked NLP position. This makes the locked NLP position effectively liquid, defeating the protocol's `NLP_LOCK_PERIOD` invariant.

---

### Finding Description

When a user deposits collateral, the subaccount identifier is derived directly from `msg.sender`:

```solidity
bytes32 subaccount = bytes32(abi.encodePacked(msg.sender, subaccountName));
```

There is no check that `msg.sender` is an EOA. A smart contract can call `depositCollateral`, creating a subaccount owned by the contract address. [1](#0-0) 

The slow-mode `LinkSigner` path also accepts any `msg.sender` as the transaction sender, and the only validation is that the embedded address in the subaccount matches the sender — which is satisfied when the smart contract itself submits the transaction:

```solidity
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [2](#0-1) 

Once a linked EOA signer is set, the EOA can sign a `MintNlp` transaction on behalf of the smart contract's subaccount. When NLP is minted, `handleNlpLockedBalance` unconditionally enqueues the minted amount with an `unlockedAt` timestamp of `getOracleTime() + NLP_LOCK_PERIOD` (4 days):

```solidity
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
});
``` [3](#0-2) 

`burnNlp` enforces this lock by requiring `getNlpUnlockedBalance(txn.sender).amount >= nlpAmount`: [4](#0-3) 

The `NLP_LOCK_PERIOD` constant is 4 days: [5](#0-4) 

The lock is enforced at the subaccount level, but the **ownership of the smart contract itself is unrestricted**. The attacker tokenizes the wrapper contract (e.g., issues ERC-20 shares), making the locked NLP position liquid at the contract-ownership layer while the underlying NLP remains locked on-chain.

---

### Impact Explanation

The 4-day NLP lock period is a core protocol invariant designed to ensure liquidity stability in the NLP pool. A wrapper smart contract that tokenizes its ownership allows users to immediately trade out of a locked NLP position by selling wrapper shares, bypassing the lock entirely. Additionally, the attacker can attract users to mint NLP through the wrapper by offering immediate liquidity or additional yield on the wrapper token, accumulating a large NLP position under a single smart contract address and concentrating influence over the NLP pool's composition.

---

### Likelihood Explanation

The attack requires only deploying a standard smart contract and issuing ERC-20 shares — both are trivial on-chain operations. No privileged access, governance capture, or leaked keys are required. The attacker-controlled entry path is fully reachable by any unprivileged actor via `depositCollateral` and the slow-mode `LinkSigner` + `MintNlp` sequencer path.

---

### Recommendation

Introduce an EOA check at the point of subaccount creation in `depositCollateral`. Reject callers whose `msg.sender` has contract code, or implement a whitelist/blacklist of approved smart contract lockers:

```solidity
require(msg.sender.code.length == 0 || isWhitelisted[msg.sender], "ERR_CONTRACT_LOCKER");
```

Alternatively, enforce the same check inside the slow-mode `LinkSigner` handler before recording the linked signer, so that smart contracts cannot delegate signing authority to bypass the EOA restriction.

---

### Proof of Concept

1. Attacker deploys `WrapperContract` which implements `depositCollateral` and `submitSlowModeTransaction` calls.
2. `WrapperContract` calls `Endpoint.depositCollateral(subaccountName, QUOTE_PRODUCT_ID, amount)` — subaccount becomes `bytes32(abi.encodePacked(address(WrapperContract), subaccountName))`. [1](#0-0) 
3. `WrapperContract` calls `Endpoint.submitSlowModeTransaction(LinkSigner{sender: subaccount, signer: attackerEOA})` — sets `attackerEOA` as the linked signer. [2](#0-1) 
4. After the 3-day slow-mode delay, the sequencer processes the `LinkSigner` transaction.
5. `attackerEOA` signs a `MintNlp` transaction for the subaccount; the sequencer processes it, minting NLP and locking it for 4 days via `handleNlpLockedBalance`. [6](#0-5) 
6. Attacker issues ERC-20 shares of `WrapperContract` to users who deposit quote tokens into it, offering immediate liquidity on the locked NLP position.
7. Users can sell their `WrapperContract` shares at any time, effectively exiting the locked NLP position before the 4-day `NLP_LOCK_PERIOD` expires — the lock is bypassed at the ownership layer. [5](#0-4)

### Citations

**File:** core/contracts/Endpoint.sol (L107-110)
```text
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
```

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

**File:** core/contracts/SpotEngine.sol (L148-167)
```text
        if (amountDelta > 0) {
            NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
                subaccount
            ];
            if (
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
                queue
                    .balances[queue.balanceCount - 1]
                    .balance
                    .amount += amountDelta;
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
            }
```

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```
