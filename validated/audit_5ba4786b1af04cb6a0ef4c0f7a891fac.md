### Title
Linked Signer Can Self-Replace Without Subaccount Owner Re-Authentication — (`File: core/contracts/EndpointTx.sol`)

### Summary
The `LinkSigner` transaction in `EndpointTx.sol` is validated with `allowLinkedSigner = true`, meaning the currently registered linked signer can authorize its own replacement with a new address — without requiring the subaccount owner's private key. This is a direct analog to the biometric self-enrollment bypass: a weaker delegated credential can permanently mutate the authorization state that governs it, without the stronger primary credential being involved.

### Finding Description
In Nado, a subaccount owner can register a `linkedSigner` — a delegated address (e.g., an API key or trading bot key) that is permitted to sign certain transactions on behalf of the subaccount. The `linkedSigners` mapping is stored in `EndpointStorage.sol`. [1](#0-0) 

When the sequencer processes a `LinkSigner` transaction through `processTransactionImpl`, it calls `validateSignedTx` with `allowLinkedSigner = true`: [2](#0-1) 

`validateSignedTx` with `allowLinkedSigner = true` routes through `validateSignature`, which passes the current `getLinkedSigner(sender)` to `verifier.validateSignature`: [3](#0-2) 

`Verifier.validateSignature` accepts the signature as valid if it recovers to either the subaccount owner address **or** the linked signer address: [4](#0-3) 

This means the existing linked signer can sign a `LinkSigner` transaction (EIP-712 typed as `LinkSigner(bytes32 sender, bytes32 signer, uint64 nonce)`) that replaces itself with any attacker-controlled address, and the protocol will accept it. [5](#0-4) 

### Impact Explanation
Once the attacker's address is installed as the new linked signer, it can authorize `WithdrawCollateral` and `WithdrawCollateralV2` (when `sendTo == address(0)`) transactions, both of which also pass `allowLinkedSigner = true`: [6](#0-5) 

The attacker can drain all collateral from the victim's subaccount across any product. The subaccount owner's private key is never needed at any step after the initial linked signer compromise.

**Impact: 4 / 10**

### Likelihood Explanation
Exploitation requires the attacker to temporarily obtain the victim's current linked signer key. Linked signers are typically API keys or session keys used by automated trading bots — a more realistic exposure surface than the primary wallet key (e.g., leaked `.env` files, compromised CI/CD pipelines, or phishing of bot operators). The attack is non-reversible once the replacement is sequenced.

**Likelihood: 2 / 10**

### Recommendation
The `LinkSigner` transaction should be validated with `allowLinkedSigner = false`, requiring the subaccount owner's primary key to authorize any change to the linked signer. This mirrors the recommended fix in the original report: re-authenticate with the stronger credential (PIN / owner key) before mutating the weaker credential (biometric / linked signer).

```solidity
// EndpointTx.sol — LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
-   true   // allowLinkedSigner
+   false  // only subaccount owner may change the linked signer
);
```

### Proof of Concept

1. Victim subaccount `0xAlice000...0001` has `linkedSigners[subaccount] = apiKeyAddress`.
2. Attacker temporarily obtains `apiKeyAddress`'s private key (e.g., leaked `.env`).
3. Attacker constructs a `SignedLinkSigner` transaction:
   - `tx.sender = 0xAlice000...0001`
   - `tx.signer = bytes32(uint256(uint160(attackerAddress)))`
   - `tx.nonce = currentNonce`
   - Signs the EIP-712 digest with `apiKeyAddress`'s key.
4. Attacker submits the transaction to the sequencer. `validateSignedTx(..., true)` passes because the recovered signer equals `apiKeyAddress == getLinkedSigner(sender)`.
5. `linkedSigners[0xAlice000...0001]` is now set to `attackerAddress`.
6. Attacker signs a `WithdrawCollateral` transaction with `attackerAddress`'s key, draining the subaccount's collateral.
7. The subaccount owner's private key was never used. [7](#0-6)

### Citations

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
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
        requireSubaccount(sender);
    }
```

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L418-436)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/Verifier.sol (L30-31)
```text
    string internal constant LINK_SIGNER_SIGNATURE =
        "LinkSigner(bytes32 sender,bytes32 signer,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L291-304)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```
