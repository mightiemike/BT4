All code references in the claim are confirmed against the production source. Let me verify the complete call chain precisely.

**Step 1** — `addLiquidityExactShares` with explicit `owner ≠ msg.sender`: [1](#0-0) 

`_validateOwner` only rejects `address(0)`, so any non-zero allowlisted address is accepted as `owner` while `msg.sender` (attacker) becomes the payer. [2](#0-1) 

**Step 2** — `_addLiquidity` calls `pool.addLiquidity(positionOwner=allowlistedOwner, ...)` with payer stored only in transient context: [3](#0-2) 

**Step 3** — Pool passes `msg.sender` (LiquidityAdder) as `sender` and `owner` (allowlisted) to the hook: [4](#0-3) 

**Step 4** — `ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [5](#0-4) 

**Step 5** — `DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (unnamed `address,`) and checks only `owner`: [6](#0-5) 

`msg.sender` here is the pool; `owner` is the allowlisted address → check passes. The actual payer (attacker) is never evaluated.

**Step 6** — Callback pulls tokens from the stored payer (attacker): [7](#0-6) 

The NatSpec intent ("Gates `addLiquidity` by depositor address") is contradicted by the implementation which gates by LP recipient: [8](#0-7) 

All six steps of the exploit chain are confirmed in production code. The vulnerability is valid.

---

Audit Report

## Title
`DepositAllowlistExtension` gates LP-share recipient instead of token payer, allowing any non-allowlisted address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[msg.sender][owner]` where `owner` is the LP-share recipient, not the address supplying tokens. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly separates `owner` (LP recipient) from `msg.sender` (payer), any non-allowlisted address can route a deposit through an allowlisted owner address, bypassing the gate entirely. The pool admin's deposit restriction — intended for compliance or KYC gating — is rendered completely ineffective.

## Finding Description

The full exploit call chain:

1. Attacker (non-allowlisted) calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedOwner, salt, deltas, max0, max1, "")`. `_validateOwner` only rejects `address(0)`, so any allowlisted address is accepted.

2. `_addLiquidity` stores `msg.sender` (attacker) as `payer` in transient context via `_setPayContext`, then calls `pool.addLiquidity(positionOwner=allowlistedOwner, ...)`.

3. The pool executes `_beforeAddLiquidity(msg.sender=LiquidityAdder, owner=allowlistedOwner, ...)`.

4. `ExtensionCalling._beforeAddLiquidity` encodes and forwards `(sender=LiquidityAdder, owner=allowlistedOwner, ...)` to the extension.

5. `DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`address,` — unnamed) and evaluates: `allowedDepositor[msg.sender][owner]` = `allowedDepositor[pool][allowlistedOwner]` → **passes**.

6. The pool proceeds; `metricOmmModifyLiquidityCallback` pulls tokens from the attacker (payer stored in transient context) and credits LP shares to the allowlisted owner.

Root cause: the `sender` parameter forwarded to the extension is the `MetricOmmPoolLiquidityAdder` contract address, not the originating caller. The actual payer is stored only in transient storage and is never surfaced to the extension hook. The extension has no mechanism to check the real depositor.

## Impact Explanation

The deposit allowlist is completely ineffective when the `MetricOmmPoolLiquidityAdder` is used. Any unprivileged address can deposit into a restricted pool by specifying any allowlisted address as `owner`. This is a direct admin-boundary break: a pool admin restriction intended for compliance, KYC gating, or institutional whitelisting is circumvented by an unprivileged public path with no special preconditions. The pool receives tokens from non-allowlisted sources in violation of the admin's configured policy.

## Likelihood Explanation

The exploit path requires no privileged access, no special setup, and no oracle manipulation. The attacker only needs to: (1) identify any allowlisted address via the public `allowedDepositor` mapping, and (2) call `addLiquidityExactShares` with that address as `owner`. The `MetricOmmPoolLiquidityAdder` is a public periphery contract. The attack is trivially repeatable on every deposit.

## Recommendation

The extension must gate on the actual token payer, not the LP-share recipient. The cleanest fix is to add `require(sender == owner)` in `beforeAddLiquidity` and check `allowedDepositor[msg.sender][sender]`. This ensures the allowlisted party is both the payer and the LP recipient, and rejects any deposit where the two are separated. Alternatively, the `MetricOmmPoolLiquidityAdder` could encode the payer into `extensionData` for the extension to decode and verify, but requiring `sender == owner` is simpler and eliminates the separation entirely.

## Proof of Concept

```solidity
function test_nonAllowlistedPayerBypassesDepositGate() public {
    address alice = makeAddr("alice"); // NOT allowlisted
    address bob   = makeAddr("bob");   // allowlisted

    vm.prank(poolAdmin);
    extension.setAllowedToDeposit(address(pool), bob, true);
    // alice is NOT set

    token0.mint(alice, 1_000e18);
    vm.prank(alice);
    token0.approve(address(liquidityAdder), type(uint256).max);

    // Alice deposits specifying bob as owner — gate should block alice but doesn't
    vm.prank(alice);
    (uint256 a0,) = liquidityAdder.addLiquidityExactShares(
        address(pool), bob, 1, delta, type(uint256).max, type(uint256).max, ""
    );

    // Deposit succeeded: alice's tokens were pulled, bob received LP shares
    assertGt(a0, 0);
    assertGt(stateView.positionBinShares(address(pool), bob, 1, binIdx), 0);
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
