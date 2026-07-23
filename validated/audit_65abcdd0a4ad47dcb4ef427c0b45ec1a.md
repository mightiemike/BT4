Audit Report

## Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Bypassing Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension::beforeSwap` receives `sender` as the immediate caller of `MetricOmmPool::swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the hook checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. This makes the per-user allowlist ineffective for router-mediated swaps, either allowing all router users through when the router is allowlisted, or blocking all allowlisted users from using the router when it is not.

## Finding Description
In `MetricOmmPool::swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`. [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall`. [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router when routing is used. [3](#0-2) 

The router calls the pool directly without forwarding the original user's identity. The `params.extensionData` bytes are passed through to the pool, but the last `bytes calldata` parameter in `beforeSwap` is unnamed and entirely unused by the hook. [4](#0-3) [5](#0-4) 

There is no mechanism in the call chain to propagate the original EOA's address to the extension.

## Impact Explanation
**Scenario A — Router is allowlisted:** A pool admin allowlists the router so that users can trade through it. Any unprivileged user can call `exactInputSingle`; the hook sees `allowedSwapper[pool][router] == true` and passes. The per-user allowlist is completely nullified — an attacker who is not individually allowlisted executes swaps in a pool designed to restrict trading to specific counterparties (e.g., a private institutional pool).

**Scenario B — Individual users are allowlisted, router is not:** Allowlisted users cannot trade through the router at all; the hook sees the router address and reverts. Core swap functionality is broken for the intended users.

Both scenarios constitute broken core pool functionality. Scenario A is the higher-impact path: an unprivileged attacker bypasses an access-control mechanism that is the sole purpose of the deployed extension.

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the standard periphery router faces this issue. Allowlisting the router is the natural and expected configuration for a pool that wants to restrict trading to a set of users while still supporting the standard swap UX. The misconfiguration is not apparent from the extension's interface or documentation.

## Recommendation
The `beforeSwap` hook should accept the original user's address through a verified channel. One approach: the router encodes the original `msg.sender` into `extensionData`, and the hook reads and verifies it (checking that `msg.sender` — the pool — is a trusted pool before trusting the encoded address). A more robust approach mirrors Uniswap v4's `hookData` pattern: the pool passes an additional `origin` parameter that the router populates with the original caller. At minimum, `SwapAllowlistExtension` documentation must explicitly warn that allowlisting the router grants unrestricted access to all router users.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender inside pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true → passes
8. Attacker's swap executes despite not being individually allowlisted

Direct call by the same attacker (no router) correctly reverts:
allowedSwapper[pool][attacker] == false → NotAllowedToSwap()
``` [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
