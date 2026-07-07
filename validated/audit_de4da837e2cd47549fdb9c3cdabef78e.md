### Title
`withdrawNative()` Rescue Path Permanently Broken — ETH Forced Into `DirectDepositV1` Is Irrecoverable — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1` accepts ETH via a `receive()` function and exposes a `withdrawNative()` rescue function. However, `withdrawNative()` sends ETH to `msg.sender` via a low-level call. The owner of every `DirectDepositV1` instance is `ContractOwner`, which has **no `receive()` or `fallback()` function**. Any ETH that lands in a DDA outside the normal wrap path (via `selfdestruct` or `CREATE2` pre-funding) is permanently irrecoverable because the only rescue path unconditionally reverts.

---

### Finding Description

`DirectDepositV1` is deployed by `ContractOwner.createDirectDepositV1()` using a deterministic `CREATE2` salt (`bytes32(uint256(1))`). The deploying contract (`ContractOwner`) becomes the Ownable owner of every DDA instance.

The `receive()` function wraps ETH immediately:

```solidity
receive() external payable {
    (bool success, ) = wrappedNative.call{value: msg.value}("");
    require(success, "Failed to wrap native token.");
}
``` [1](#0-0) 

If wrapping fails, the call reverts — ETH cannot enter via this path. However, two paths bypass `receive()` entirely and leave raw ETH in the DDA:

1. **`selfdestruct` targeting the DDA address**: Any unprivileged user can deploy a contract funded with ETH and `selfdestruct` it to the DDA address. `selfdestruct` transfers ETH directly to the target balance, bypassing `receive()`.

2. **`CREATE2` pre-funding**: Because the DDA salt is a hardcoded constant (`bytes32(uint256(1))`), the DDA address is predictable before deployment. An attacker can send ETH to that address before `createDirectDepositV1()` is called. The constructor handles this case but explicitly does not revert on wrapping failure:

```solidity
if (!success) {
    emit NativeTokenTransferFailed(balance);
}
``` [2](#0-1) 

The comment at line 54–55 reads: *"shouldn't revert even if the transfer fails, otherwise the funds will be stuck in the DDA forever."* — but the rescue path is itself broken.

The intended rescue is `withdrawNative()`:

```solidity
function withdrawNative() external onlyOwner {
    uint256 balance = address(this).balance;
    (bool success, ) = msg.sender.call{value: balance}("");
    require(success, "Failed to transfer native token to owner");
}
``` [3](#0-2) 

`msg.sender` here is `ContractOwner`. `ContractOwner` defines no `receive()` or `fallback()` function: [4](#0-3) 

The low-level call `ContractOwner.call{value: balance}("")` will therefore fail. `withdrawNative()` reverts with `"Failed to transfer native token to owner"`.

`ContractOwner.withdrawFromDirectDepositV1()` — the only higher-level rescue path — calls `withdrawNative()` and then checks `postBalance > preBalance`:

```solidity
uint256 preBalance = address(this).balance;
DirectDepositV1(directDepositV1).withdrawNative();
uint256 postBalance = address(this).balance;
require(postBalance > preBalance, "empty");
``` [5](#0-4) 

This call chain reverts entirely. There is no alternative rescue path in the codebase.

---

### Impact Explanation

ETH forced into any `DirectDepositV1` instance via `selfdestruct` or `CREATE2` pre-funding is permanently locked. The only defined rescue function (`withdrawNative()`) unconditionally reverts when called through the owner contract because `ContractOwner` cannot receive ETH. The funds are irrecoverable with no fallback mechanism.

---

### Likelihood Explanation

The DDA salt is a hardcoded constant, making every DDA address predictable before deployment. Any user can pre-fund the address or `selfdestruct` ETH into a live DDA. The cost is only the ETH sent plus gas. The broken rescue path means the impact is permanent regardless of amount.

---

### Recommendation

Add a `receive()` function to `ContractOwner` so it can accept ETH forwarded from `withdrawNative()`:

```solidity
receive() external payable {}
```

Alternatively, modify `withdrawNative()` in `DirectDepositV1` to accept a `recipient` parameter so ETH can be sent directly to an EOA rather than through `ContractOwner`.

---

### Proof of Concept

1. Attacker deploys a contract funded with 1 ETH and calls `selfdestruct(payable(ddaAddress))`.
2. `DirectDepositV1` at `ddaAddress` now holds 1 ETH in its raw balance (bypassing `receive()`).
3. Owner calls `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))`.
4. Internally: `DirectDepositV1.withdrawNative()` executes `ContractOwner.call{value: 1 ether}("")`.
5. `ContractOwner` has no `receive()` → call returns `success = false`.
6. `withdrawNative()` reverts: `"Failed to transfer native token to owner"`.
7. The 1 ETH is permanently stuck in the DDA with no rescue path.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L52-59)
```text
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
```

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L108-112)
```text
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/ContractOwner.sol (L21-68)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
    error InvalidInput();
    using MathSD21x18 for int128;
    using ERC20Helper for IERC20Base;

    address internal deployer;
    SpotEngine internal spotEngine;
    PerpEngine internal perpEngine;
    Endpoint internal endpoint;
    IClearinghouse internal clearinghouse;
    Verifier internal verifier;
    address payable internal wrappedNative;

    bytes[] internal updateProductTxs; // deprecated
    bytes[] internal rawSpotAddProductCalls; // deprecated
    bytes[] internal rawPerpAddProductCalls; // deprecated

    mapping(bytes32 => address payable) public directDepositV1Address;

    bytes[] internal rawSpotAddOrUpdateProductCalls;
    bytes[] internal rawPerpAddOrUpdateProductCalls;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        address multisig,
        address _deployer,
        address _spotEngine,
        address _perpEngine,
        address _endpoint,
        address _clearinghouse,
        address _verifier,
        address payable _wrappedNative
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
        spotEngine = SpotEngine(_spotEngine);
        perpEngine = PerpEngine(_perpEngine);
        endpoint = Endpoint(_endpoint);
        clearinghouse = IClearinghouse(_clearinghouse);
        verifier = Verifier(_verifier);
        wrappedNative = _wrappedNative;
    }
```

**File:** core/contracts/ContractOwner.sol (L629-636)
```text
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
```
