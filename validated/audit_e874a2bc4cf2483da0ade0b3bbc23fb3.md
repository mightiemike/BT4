### Title
Minimum First-Deposit Bypass via Withdrawal — (`core/contracts/Endpoint.sol` / `core/contracts/Clearinghouse.sol`)

---

### Summary

`depositCollateral` enforces a `MIN_FIRST_DEPOSIT_AMOUNT` ($5) for new subaccounts, but `withdrawCollateral` only checks that post-withdrawal health is ≥ 0. A user can satisfy the first-deposit gate, then immediately submit a slow-mode withdrawal that reduces their balance below the $5 floor, permanently registering a subaccount with far less collateral than the protocol intends.

---

### Finding Description

**Entry gate — deposit side**

`isValidDepositAmount` in `Endpoint.sol` selects the minimum based on whether the subaccount already exists:

```solidity
int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;          // $0.10
if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
    minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;        // $5.00
}
return clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
``` [1](#0-0) 

`depositCollateral` hard-reverts if this check fails, so a brand-new subaccount must deposit ≥ $5. [2](#0-1) 

**Exit path — withdrawal side**

`withdrawCollateral` in `Clearinghouse.sol` imposes **no minimum remaining balance**. It only verifies utilization and that health ≥ 0 after the deduction:

```solidity
spotEngine.updateBalance(productId, sender, amountRealized);
spotEngine.assertUtilization(productId);
...
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [3](#0-2) 

For a subaccount holding only quote collateral with no open positions, health equals the remaining balance, so withdrawing down to $0.10 (or even $0) passes the health check.

**Constants confirming the gap** [4](#0-3) 

`MIN_FIRST_DEPOSIT_AMOUNT = $5`, `MIN_DEPOSIT_AMOUNT = $0.10` — a 50× difference that the withdrawal path never enforces.

---

### Impact Explanation

An attacker can register a subaccount for an effective cost of ~$0.10 (plus the $1 slow-mode fee and gas) instead of the intended $5:

1. Deposit exactly $5 → subaccount is registered (`subaccountIds[subaccount]` becomes non-zero).
2. Submit a slow-mode `WithdrawCollateral` transaction for $3.90 (the $1 slow-mode fee is charged from the balance, leaving $0.10 after execution).
3. After the 3-day delay, `executeSlowModeTransaction` processes the withdrawal; health check passes because $0.10 ≥ 0.

The broken invariant: *every registered subaccount must have been created with ≥ $5 in collateral*. After the bypass, the subaccount is permanently registered with $0.10. All future deposits to it only need to meet `MIN_DEPOSIT_AMOUNT` ($0.10), so the first-deposit gate is permanently circumvented for that address/name pair.

Concrete corrupted state: `subaccountIds[subaccount] != 0` while `spotEngine.getBalance(QUOTE_PRODUCT_ID, subaccount).amount < MIN_FIRST_DEPOSIT_AMOUNT`.

---

### Likelihood Explanation

**Medium.** Any unprivileged user can execute this with no special access. The only friction is the 3-day slow-mode delay (`SLOW_MODE_TX_DELAY`) and the $1 slow-mode fee. The fee makes mass spam expensive but does not prevent a targeted bypass. The sequencer can also process `WithdrawCollateral` transactions out-of-band via `submitTransactionsChecked`, which removes the delay entirely (though that path requires sequencer cooperation). [5](#0-4) 

---

### Recommendation

In `withdrawCollateral`, after updating the balance, assert that the remaining balance is either zero (full exit) or at or above `MIN_DEPOSIT_AMOUNT`. Concretely:

```solidity
int128 remaining = spotEngine.getBalance(productId, sender).amount;
require(
    remaining == 0 || remaining >= int128(MIN_DEPOSIT_AMOUNT),
    ERR_DEPOSIT_TOO_SMALL
);
```

This mirrors the Tortuga remediation: a delegator must either maintain the minimum or withdraw everything.

---

### Proof of Concept

```
// Assume: attacker controls EOA `alice`, subaccountName = "default"
// subaccount = bytes32(abi.encodePacked(alice, "default"))
// subaccountIds[subaccount] == 0  →  minDepositAmount = $5

1. alice calls Endpoint.depositCollateral("default", QUOTE_PRODUCT_ID, 5e6)
   → isValidDepositAmount passes ($5 >= $5)
   → subaccount registered, balance = $5

2. alice calls Endpoint.submitSlowModeTransaction(
       abi.encodePacked(
           uint8(TransactionType.WithdrawCollateral),
           abi.encode(WithdrawCollateral({sender: subaccount, productId: 0, amount: 3_900_000, nonce: 0}))
       )
   )
   → $1 slow-mode fee deducted → balance = $4
   → tx queued with executableAt = now + 3 days

3. After 3 days, anyone calls Endpoint.executeSlowModeTransaction()
   → Clearinghouse.withdrawCollateral executes
   → balance = $4 - $3.90 = $0.10
   → getHealth returns $0.10 >= 0  ✓
   → Withdrawal succeeds

Result: subaccount permanently registered with $0.10 balance,
        bypassing the $5 MIN_FIRST_DEPOSIT_AMOUNT gate.
```

### Citations

**File:** core/contracts/Endpoint.sol (L94-101)
```text
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
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

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
