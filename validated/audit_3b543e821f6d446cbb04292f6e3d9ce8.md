### Title
Order Digest Domain Separator Uses Product ID as Verifying Contract Instead of Contract Address — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` manually constructs an EIP-712 domain separator that substitutes `address(uint160(productId))` for the `verifyingContract` field instead of `address(this)`. Because the domain separator does not bind to the actual `OffchainExchange` contract address, signed orders are not contract-specific and can be replayed against any contract that constructs the same domain separator.

---

### Finding Description

`OffchainExchange.getDigest()` builds its own domain separator rather than delegating to the inherited `_hashTypedDataV4`:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← NOT address(this)
    )
);
return ECDSAUpgradeable.toTypedDataHash(domainSeparator, structHash);
``` [1](#0-0) 

The EIP-712 specification requires `verifyingContract` to be the address of the contract that will verify the signature. Here it is set to `address(uint160(productId))` — a `uint32` product identifier cast to an address — which resolves to a trivially predictable, near-zero address (e.g., `0x0000…0001` for `productId = 1`). This address is not the `OffchainExchange` contract and is not a deployed contract at all.

By contrast, all other signed transaction types in the protocol (withdraw, liquidate, link signer, etc.) correctly use `_hashTypedDataV4`, which wraps the struct hash with the `Endpoint`'s domain separator that includes `address(this)`:

```solidity
_hashTypedDataV4(
    computeDigest(
        IEndpoint.TransactionType(uint8(transaction[0])),
        transaction[1:]
    )
)
``` [2](#0-1) 

The `Endpoint` is initialized with `__EIP712_init("Nado", "0.0.1")`, binding its domain separator to its own address: [3](#0-2) 

`OffchainExchange.getDigest()` bypasses this entirely and produces a domain separator whose `verifyingContract` field is meaningless.

---

### Impact Explanation

**Cross-contract order replay.** Because the domain separator does not include the real `OffchainExchange` address, a signed order is valid for any contract that constructs the same domain separator (same name hash, version hash, `chainId`, and `productId`). An attacker can:

1. Deploy a contract that presents itself as a legitimate trading interface and uses the identical domain separator construction.
2. Trick a user into signing an `Order` struct (e.g., a large sell order at a low price) under the belief they are interacting with a different application.
3. Submit that signature to the real `OffchainExchange.matchOrders` path via the sequencer.
4. The signature passes `_checkSignature` because the digest is identical — the domain separator does not distinguish the real contract from the attacker's contract. [4](#0-3) 

**Post-migration replay.** If the protocol ever deploys a new `OffchainExchange` proxy (fresh storage, fresh `filledAmounts`), all previously signed orders are immediately replayable against the new deployment. The `filledAmounts` guard that prevents double-fill within a single deployment is reset, but the domain separator is unchanged because it never referenced the old contract address. [5](#0-4) 

The concrete corrupted state is: `filledAmounts[digest]` and the subaccount balances in `SpotEngine`/`PerpEngine` — an attacker can force a user's position to be opened, closed, or partially filled at an adversarially chosen price without the user's current consent.

---

### Likelihood Explanation

**Medium.** The phishing vector requires the attacker to operate a DApp that presents a plausible signing request. Because the domain separator's `verifyingContract` is `address(uint160(productId))` (a near-zero address with no human-readable name), a wallet like MetaMask will display the verifying contract as an anonymous address rather than "Nado / OffchainExchange", making it harder for a security-conscious user to detect the mismatch. The migration replay vector requires a protocol redeployment, which is a realistic operational event for an upgradeable system. No privileged access is required for the attacker; only a valid ECDSA signature from the victim is needed, and the sequencer will process any well-formed `MatchOrders` transaction.

---

### Recommendation

Replace the manually constructed domain separator in `getDigest` with `_hashTypedDataV4`, using the `OffchainExchange` contract's own EIP-712 domain (initialized via `__EIP712_init` in its `initialize` function). The product-scoping currently achieved by embedding `productId` in the domain separator should instead be encoded in the struct type hash or as a field in the `Order` struct itself (which already contains `sender`, `priceX18`, `amount`, `expiration`, `nonce`, `appendix`).

```solidity
// Before (vulnerable)
bytes32 domainSeparator = keccak256(abi.encode(
    _TYPE_HASH, _EIP712NameHash(), _EIP712VersionHash(),
    block.chainid, address(uint160(productId))
));
return ECDSAUpgradeable.toTypedDataHash(domainSeparator, structHash);

// After (correct)
return _hashTypedDataV4(structHash);
// where structHash encodes productId as a field in the Order struct type
``` [6](#0-5) 

---

### Proof of Concept

1. Attacker deploys `MaliciousDApp` on the same chain. Its `getDigest(productId, order)` function is a copy of `OffchainExchange.getDigest` — it produces an identical domain separator because `address(uint160(productId))` is the same regardless of which contract calls it.
2. Victim connects to `MaliciousDApp` and signs an `Order` struct (e.g., sell 100 ETH-PERP at price 1). The wallet shows `verifyingContract: 0x0000000000000000000000000000000000000001` — indistinguishable from the real exchange's display.
3. Attacker submits a `MatchOrders` transaction to the real `Endpoint` sequencer queue, pairing the victim's signed order as taker against an attacker-controlled maker order at the adversarial price.
4. `OffchainExchange._checkSignature` recovers the signer from the digest produced by `getDigest(productId, order)`. The digest is byte-for-byte identical to what the victim signed. Signature check passes.
5. The trade executes: victim's subaccount balance is debited, attacker's maker subaccount is credited. [1](#0-0) [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L30-30)
```text
    mapping(bytes32 => int128) public filledAmounts;
```

**File:** core/contracts/OffchainExchange.sol (L291-322)
```text
    function getDigest(uint32 productId, IEndpoint.Order memory order)
        public
        view
        returns (bytes32)
    {
        string
            memory structType = "Order(bytes32 sender,int128 priceX18,int128 amount,uint64 expiration,uint64 nonce,uint128 appendix)";

        bytes32 structHash = keccak256(
            abi.encode(
                keccak256(bytes(structType)),
                order.sender,
                order.priceX18,
                order.amount,
                order.expiration,
                order.nonce,
                order.appendix
            )
        );

        bytes32 domainSeparator = keccak256(
            abi.encode(
                _TYPE_HASH,
                _EIP712NameHash(),
                _EIP712VersionHash(),
                block.chainid,
                address(uint160(productId))
            )
        );

        return ECDSAUpgradeable.toTypedDataHash(domainSeparator, structHash);
    }
```

**File:** core/contracts/OffchainExchange.sol (L332-343)
```text
    function _checkSignature(
        bytes32 subaccount,
        bytes32 digest,
        address linkedSigner,
        bytes memory signature
    ) internal view virtual returns (bool) {
        address signer = ECDSA.recover(digest, signature);
        return
            (signer != address(0)) &&
            (signer == address(uint160(bytes20(subaccount))) ||
                signer == linkedSigner);
    }
```

**File:** core/contracts/EndpointTx.sol (L94-104)
```text
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
```

**File:** core/contracts/Endpoint.sol (L39-41)
```text
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
```
