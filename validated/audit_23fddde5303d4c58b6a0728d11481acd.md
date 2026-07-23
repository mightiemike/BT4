All five cited files are confirmed in the repository. The call chain is fully verified:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` — when called via the router, `msg.sender` is the router contract.
2. `ExtensionCalling._beforeSwap()` forwards `sender` unchanged to every configured extension.
3. `SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router.
4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap(...)` with no mechanism to forward the original user's identity; `extensionData` is caller-supplied and unenforced.
5. `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (the explicit position owner argument), not `sender`, confirming the asymmetry is real and unique to the swap path.

---

Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct pool caller. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. If the pool admin allowlists the router (the natural step to let curated users access the pool via the official periphery), every unprivileged user can bypass the allowlist by routing through the router, as the extension sees only the router address and approves the call.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct pool caller, not the economic actor
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, the router calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

No original-user identity is forwarded. The router passes `params.extensionData` as supplied by the caller, but the extension does not decode or enforce any user attestation from it. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two concrete failure modes:**
1. **Allowlist bypass (high impact):** Admin allowlists the router so curated users can use the official periphery. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and permits the swap. The allowlist invariant is broken for all users.
2. **Broken allowlist (medium impact):** Admin does not allowlist the router. Allowlisted users cannot use the router at all; they must call the pool directly. The official periphery path is silently unusable for curated pools.

**Why existing guards fail:** The only guard is `allowedSwapper[pool][sender]`. There is no secondary check on the original transaction initiator, no `tx.origin` check, and no trusted attestation mechanism in `extensionData`. `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the explicit position-owner argument passed by the caller), not `sender`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized traders can move pool prices, extract value against LP positions, and generate swap volume without the intended gate. This constitutes a direct admin-boundary break — an unprivileged path bypasses a pool admin's access control configuration — with fund-impacting consequences for LPs in curated pools. Severity: High.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the official, documented swap periphery. Pool admins who configure a swap allowlist will naturally also want their allowlisted users to be able to use the router, making the router-allowlisting step highly probable. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup, no privileged role, and no additional cost beyond a normal swap call. The attack is repeatable on every swap.

## Recommendation
The extension must gate the **original user**, not the direct pool caller. Viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it against a trusted-router registry. Fragile if multiple entry points exist.
2. **Preferred — add a first-class `originalSender` field:** Propagate the economic actor through the hook arguments at the pool level so extensions always see the true swapper regardless of intermediary.
3. **Router-aware allowlist:** Extend the extension to recognize approved router contracts and, when `sender` is a known router, require a signed user attestation in `extensionData`.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as extension1
  admin calls setAllowedToSwap(pool, router, true)   // allowedSwapper[pool][router] = true
  admin calls setAllowedToSwap(pool, alice, true)    // allowedSwapper[pool][alice] = true
  bob (NOT allowlisted): allowedSwapper[pool][bob] = false

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)          // msg.sender = router
  → pool calls _beforeSwap(router, ...)
  → extension checks allowedSwapper[pool][router] → true
  → swap executes for bob with no revert

Result:
  bob, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist invariant is broken.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin allowlists router address.
  3. Prank as bob (non-allowlisted).
  4. Call router.exactInputSingle targeting the pool.
  5. Assert swap succeeds (no NotAllowedToSwap revert).
```