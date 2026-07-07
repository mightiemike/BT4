### Title
Unauthorized Forced Deposit to Arbitrary Subaccount via Missing Ownership Check — (`core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` is a `public` function that accepts an arbitrary `bytes32 subaccount` parameter with no check that `msg.sender` owns that subaccount. Any caller can force a deposit into any non-isolated subaccount of any address, mutating the victim's balance without their consent.

---

### Finding Description

`depositCollateral` (the sibling function) correctly derives the subaccount from `msg.sender`:

```solidity
bytes32 subaccount = bytes32(abi.encodePacked(msg.sender, subaccountName));
``` [1](#0-0) 

`depositCollateralWithReferral`, however, accepts a raw `bytes32 subaccount` from the caller with no equivalent ownership binding:

```solidity
function depositCollateralWithReferral(
    bytes32 subaccount,
    uint32 productId,
    uint128 amount,
    string memory
) public {
    require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);
    address sender = address(bytes20(subaccount));
    ...
``` [2](#0-1) 

The only guards are: (1) isolated-subaccount rejection, (2) sanctions checks on both parties, and (3) a minimum deposit amount. None of these verify that `msg.sender` is the owner of `subaccount`.

The token transfer pulls from `msg.sender` (the attacker pays), and a slow-mode tx is queued with `sender: address(bytes20(subaccount))` — the victim's address — and payload `DepositCollateral{sender: victimSubaccount, ...}`: [3](#0-2) 

When the slow-mode tx executes, `validateSender(txn.sender, sender)` passes trivially because the attacker already stored the victim's address as `sender` when queuing: [4](#0-3) 

Then `clearinghouse.depositCollateral(txn)` is called, which calls `spotEngine.updateBalance(txn.productId, txn.sender, amountRealized)`, crediting the victim's subaccount: [5](#0-4) 

---

### Impact Explanation

The invariant **"balance mutations must only occur with the subaccount owner's authorization"** is broken. Concretely:

1. **Unauthorized balance mutation**: Any non-isolated subaccount of any address can have its spot balance increased without the owner's consent. This is a direct violation of the Critical scope: *"Unauthorized mutation of another user's subaccount balances."*

2. **Liquidation blocking**: A victim's subaccount that is at or near the maintenance health threshold can be pushed above it by a forced deposit, causing `isUnderMaintenance` to return `false` and reverting any pending liquidation call: [6](#0-5) 
   The 3-day slow-mode delay (`SLOW_MODE_TX_DELAY`) means the attacker must act in advance, but for positions with slowly deteriorating health this is feasible. The sequencer can also process slow-mode txs immediately (`fromSequencer = true`), which removes the delay entirely if the sequencer is cooperative or compromised.

3. **Unwanted subaccount state**: A victim who deliberately keeps a subaccount at zero balance (to avoid fees, interest accrual, or other accounting effects) has that state forcibly changed.

---

### Likelihood Explanation

The attack path is fully permissionless — any EOA or contract can call `depositCollateralWithReferral` with an arbitrary `subaccount`. The attacker's only cost is the deposited tokens themselves (which go to the victim's account, not lost). The minimum deposit threshold is `$0.1` for existing subaccounts: [7](#0-6) 

This makes the attack economically viable for any attacker willing to spend small amounts to manipulate victim state.

---

### Recommendation

Add an ownership check in `depositCollateralWithReferral` mirroring the pattern in `depositCollateral`:

```solidity
require(
    address(bytes20(subaccount)) == msg.sender,
    ERR_UNAUTHORIZED
);
```

This ensures only the address encoded in the subaccount's upper 20 bytes can initiate a deposit to that subaccount, consistent with the design of `depositCollateral`.

---

### Proof of Concept

```solidity
// Hardhat test (chainId 31337 — slow-mode executes synchronously per Endpoint.sol:201-203)
it("force deposit to victim subaccount without consent", async () => {
    const [attacker, victim] = await ethers.getSigners();
    const victimSubaccount = ethers.utils.hexZeroPad(
        ethers.utils.hexlify(victim.address) + "6465666175", // "defau" in hex = subaccount name
        32
    );

    // Attacker approves quote token and calls depositCollateralWithReferral
    await quoteToken.connect(attacker).approve(endpoint.address, depositAmount);
    await endpoint.connect(attacker).depositCollateralWithReferral(
        victimSubaccount,
        QUOTE_PRODUCT_ID,
        depositAmount,
        "-1"
    );

    // Execute slow-mode tx (immediate on chainId 31337)
    await endpoint.executeSlowModeTransaction();

    // Assert: victim's subaccount balance increased without victim's action
    const balance = await spotEngine.getBalance(QUOTE_PRODUCT_ID, victimSubaccount);
    assert(balance.amount > 0, "Victim balance mutated without consent");
});
```

The slow-mode execution path on Hardhat (`chainId == 31337`) calls `this.processSlowModeTransaction` directly without the `try/catch` wrapper, making the state change immediately observable and locally testable without any sequencer involvement. [8](#0-7)

### Citations

**File:** core/contracts/Endpoint.sol (L108-110)
```text
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
```

**File:** core/contracts/Endpoint.sol (L123-135)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/Endpoint.sol (L144-165)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

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
```

**File:** core/contracts/Endpoint.sol (L201-203)
```text
        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
```

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L207-208)
```text
        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/ClearinghouseLiq.sol (L601-603)
```text
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```
