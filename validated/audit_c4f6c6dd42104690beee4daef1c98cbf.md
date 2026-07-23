All four files are confirmed in the repository. The code matches the claim exactly. Let me verify the key details:

- `DepositAllowlistExtension.beforeAddLiquidity` at line 32 silently drops the first `address` argument (`sender`) and checks `allowedDepositor[msg.sender][owner]` — where `msg.sender` is the pool and `owner` is the caller-supplied position recipient.
- `MetricOmmPool.addLiquidity` at line 191 passes `msg.sender` (the adder contract) as `sender` and the caller-supplied `owner` as `owner`.
- `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` at line 65 only validates `owner != address(0)`, allowing any non-zero address to be supplied.
- The actual payer (`userB`) is stored only in transient storage and used in the callback — it is never surfaced to the extension.

The attack path is fully reachable with no elevated privilege.

---

Audit Report

## Title
`DepositAllowlistExtension` checks position recipient (`owner`) instead of actual depositor (`sender`), allowing allowlist bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates solely on `owner` (the position recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` permits any caller to supply an arbitrary non-zero `owner`, an unprivileged user can bypass the deposit allowlist by naming an allowlisted address as `owner`, causing the pool to accept tokens from a non-allowlisted actor while LP shares flow to the named recipient.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` is declared with the first positional argument (`sender`) unnamed and unused:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool (the extension is called by the pool via `CallExtension.callExtension`). The check therefore evaluates `allowedDepositor[pool][owner]` — i.e., whether the *position recipient* is on the allowlist — not whether the actual depositor/payer is allowed.

`MetricOmmPool.addLiquidity` passes `msg.sender` (the adder contract) as `sender` and the caller-supplied `owner` as `owner`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both arguments: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the owner-explicit overload) validates only that `owner != address(0)`: [3](#0-2) 

The actual payer (`msg.sender` of `addLiquidityExactShares`) is stored only in transient storage and surfaced only in the callback — it is never passed to the extension: [4](#0-3) 

**Full attack path:**
1. Pool admin deploys a curated pool with `DepositAllowlistExtension` and allowlists `userA`.
2. `userB` (not allowlisted) calls `liquidityAdder.addLiquidityExactShares(pool, owner=userA, ...)`.
3. The adder calls `pool.addLiquidity(owner=userA, ...)` — `msg.sender` to the pool is the adder.
4. The pool calls `_beforeAddLiquidity(sender=liquidityAdder, owner=userA, ...)`.
5. The extension evaluates `allowedDepositor[pool][userA]` → `true` → does not revert.
6. `userB`'s tokens are pulled in the callback; `userA` receives the LP shares.
7. The pool has accepted a deposit from a non-allowlisted actor.

The same path applies to `addLiquidityWeighted` (owner-explicit overload): the probe call passes `owner=userA` to the extension, the check passes, and the subsequent paying call is identical. [5](#0-4) 

## Impact Explanation
The deposit allowlist is rendered entirely ineffective. Any unprivileged user can deposit into a curated pool by naming an allowlisted address as `owner`. The pool receives tokens from a non-allowlisted actor, directly violating the pool admin's access-control policy. For KYC-gated or regulated pools this constitutes a direct admin-boundary break: the restriction the pool admin configured is bypassed by a standard periphery call with no elevated privilege. This meets the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" allowed impact criterion. [6](#0-5) 

## Likelihood Explanation
Medium. The bypass requires no admin cooperation and no special privilege — only the ability to call `addLiquidityExactShares` or `addLiquidityWeighted` with an allowlisted address as `owner`. The allowlisted address need not cooperate; the attacker only needs to know one allowlisted address (which may be observable on-chain from prior `AllowedToDepositSet` events). The attack is repeatable and leaves no on-chain trace distinguishing it from a legitimate deposit by `userA`. [7](#0-6) 

## Recommendation
The extension must gate on the actual depositor, not the position recipient. The correct fix is to use the first positional argument (`sender`) — which is `msg.sender` of `pool.addLiquidity()` — rather than `owner`. However, note that when the periphery adder is used, `sender` is the adder contract, not the end user. A complete fix requires either: (a) checking `sender` and requiring that the adder itself be allowlisted (coarse-grained), or (b) having the adder encode the actual payer address in `extensionData` and having the extension decode and check it. The simplest correct fix for direct pool callers is:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

For periphery-routed calls, the adder should encode the actual payer in `extensionData` and the extension should decode it.

## Proof of Concept
```solidity
// Foundry unit test sketch
function test_allowlistBypass() public {
    // Setup: pool with DepositAllowlistExtension, userA allowlisted, userB not
    address pool = _deployPoolWithAllowlistExtension();
    extension.setAllowedToDeposit(pool, userA, true);

    // userB approves the adder and calls addLiquidityExactShares with owner=userA
    vm.startPrank(userB);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);
    // This should revert if allowlist is enforced, but it succeeds:
    adder.addLiquidityExactShares(pool, userA, salt, deltas, max0, max1, "");
    vm.stopPrank();

    // userA now holds LP shares funded by userB's tokens
    // Pool accepted deposit from non-allowlisted actor
}
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L100-116)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-164)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
