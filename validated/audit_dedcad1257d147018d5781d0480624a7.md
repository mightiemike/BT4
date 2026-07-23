Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual end-user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's direct `msg.sender` — the router contract — not the originating user. Because `MetricOmmPool.swap` has no explicit "originating swapper" parameter, routing through `MetricOmmSimpleRouter` substitutes the router's address for the user's address in the allowlist check. If the router is allowlisted (required for any allowlisted user to trade via the standard periphery), every unprivileged address can bypass the gate by calling the router. The deposit allowlist does not share this flaw because `addLiquidity` carries an explicit `owner` parameter that the liquidity adder populates with the real user.

## Finding Description

**Root cause — no explicit swapper identity in the swap call path:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router, not the end-user
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

the pool's `msg.sender` is the router, so `sender = router` reaches the extension. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Why the deposit path does not share this flaw:**

`MetricOmmPool.addLiquidity` accepts an explicit `owner` parameter:

```solidity
// metric-core/contracts/MetricOmmPool.sol L182-191
function addLiquidity(address owner, ...) external ... {
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`MetricOmmPoolLiquidityAdder` populates `owner` with the real user's address, so `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `allowedDepositor[pool][owner]` — the actual depositor — regardless of who the intermediary caller is. The swap interface has no equivalent field; the swapper identity is always inferred from `msg.sender` at the pool boundary.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only Alice: `allowedSwapper[pool][alice] = true`.
2. Pool admin also allowlists the router so Alice can trade via the standard periphery: `allowedSwapper[pool][router] = true`.
3. Mallory (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` → pool's `msg.sender = router`.
5. Extension receives `sender = router`; checks `allowedSwapper[pool][router] == true` → permits the swap.
6. Mallory's swap executes on the restricted pool despite not being on the allowlist.

**Existing guards are insufficient:** There is no on-chain mechanism in the current extension interface to recover the originating user's identity from within `beforeSwap`. The `extensionData` field is caller-controlled and unauthenticated, so it cannot be trusted without a signed attestation scheme.

## Impact Explanation

This is an admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path. Any address can execute swaps on pools intended to be restricted, as long as the router is allowlisted. The pool admin is forced into an impossible choice — either allowlist the router (making the allowlist ineffective for all router-mediated swaps) or do not allowlist the router (making the router unusable for legitimate allowlisted users). The allowlist extension's core invariant — that only explicitly permitted addresses may swap — is completely violated for the router path.

## Likelihood Explanation

Both required conditions (pool configured with `SwapAllowlistExtension`, router allowlisted) are expected in normal production use. The bypass requires no special privileges, no flash loans, no price manipulation, and no contract deployment — any EOA can call `MetricOmmSimpleRouter` directly. The attack is repeatable on every block and on every pool that uses this extension with the router allowlisted.

## Recommendation

The `beforeSwap` hook interface must be extended to carry an authenticated originating-user identity, or the router must embed the real `msg.sender` in `extensionData` and the extension must verify it. The minimal fix consistent with the existing `DepositAllowlistExtension` pattern is to add an explicit `swapper` parameter to the `beforeSwap` interface (analogous to `owner` in `beforeAddLiquidity`), have the pool populate it with `tx.origin` or a router-attested value, and gate on that field instead of `sender`. Alternatively, require the router to ABI-encode `msg.sender` into `extensionData` and have the extension decode and check it, accepting only calls where `sender == router` and the encoded address is allowlisted.

## Proof of Concept

```solidity
// Setup
allowlistExt.setAllowedToSwap(pool, alice, true);
allowlistExt.setAllowedToSwap(pool, address(router), true); // required for Alice to use router

// Attack: Mallory (not allowlisted) routes through the router
vm.prank(mallory);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: mallory,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// router calls pool.swap() → pool passes msg.sender=router to _beforeSwap
// extension checks allowedSwapper[pool][router] == true → swap succeeds
// Mallory receives output tokens despite not being on the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
