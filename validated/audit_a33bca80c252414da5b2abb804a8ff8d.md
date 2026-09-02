Based on my review of the in-scope Bridge contract functions (`deposit`, `withdraw`, `safeWithdraw`, `batchWithdraw`, `replaceDeposit`), none of them exhibit the FrankenDAO bug pattern — there is no delegated/approved third-party action where bookkeeping updates the wrong party's balance instead of the intended owner's.

Key observations:
- `withdraw()` and `batchWithdraw()` require `msg.value == depositAmount` and directly burn the caller's own sent value; there is no "act on behalf of an approved owner" concept, so there's no owner/msg.sender bookkeeping mismatch to exploit. [1](#0-0) 
- `safeWithdraw()` validates a specific Bitcoin-side signature over the payout transaction and script pubkey before calling `withdraw()`, but the cBTC burned is still `msg.value` from `msg.sender`, and the recipient of the eventual BTC is bound by the validated `withdrawalAddressPubKey`/signature, not by an owner-approval bookkeeping structure. [2](#0-1) 
- `deposit()` mints cBTC to the `recipient` address extracted from the verified Bitcoin script, not to `msg.sender`, so there is no owner-vs-caller confusion there either.
<invoke name="grep_search">
<parameter name="pattern">recipient.call</parameter>
<parameter name="repos">["Oyahkilomeikhide/citrea--024"]</parameter>
</invoke>

### Citations

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L246-260)
```text
    function withdraw(bytes32 txId, bytes4 outputId) public payable whenNotPaused {
        require(msg.value == depositAmount, "Invalid withdraw amount");

        bytes32 utxoKey = sha256(abi.encodePacked(txId, outputId));
        require(!usedWithdrawalUTXO[utxoKey], "UTXO already used");
        usedWithdrawalUTXO[utxoKey] = true;

        UTXO memory utxo = UTXO({
            txId: txId,
            outputId: outputId
        });
        uint256 index = withdrawalUTXOs.length;
        withdrawalUTXOs.push(utxo);
        emit Withdrawal(utxo, index, block.timestamp);
    }
```

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L288-323)
```text
        // Assert that the payout output value is the expected optimistic withdraw amount
        require(uint256(payoutOutput.extractValue()) == optimisticWithdrawAmountSats, "Payout output value does not match optimistic withdraw amount");

        // Assert the user provided script pubkey is the same as the one in the payout transaction's output
        (uint256 varIntDataLen, uint256 pubKeyLen) = BTCUtils.parseVarIntAt(payoutOutput, 8);
        require(isBytesEqual(payoutOutput.slice(9 + varIntDataLen, pubKeyLen), withdrawalAddressPubKey), "Invalid payout output script pubkey");

        // Payout tx should spend the prepare tx, so we need to check if the txId of the input matches the txId of the prepare transaction
        bytes32 spentTxId = payoutInput.extractInputTxIdLE();
        require(spentTxId == txId, "Invalid spent txId");

        // Assert that the spent output is a P2TR output and that the script pubkey is the same as the one provided in parameters
        bytes4 spentIndex = payoutInput.extractTxIndexLE();
        bytes memory spentOutput = prepareTx.vout.extractOutputAtIndex(BTCUtils.reverseUint32(uint32(spentIndex)));
        require(spentOutput.length == 43, "Invalid spent output length"); // 8 bytes for amount + 1 byte for script pub key length + 2 bytes for OP_1 OP_PUSHBYTES32 + 32 bytes for the hash
        require(isBytesEqual(spentOutput.slice(8, 1), hex"22"), "Invalid spent output script pubkey length");
        require(isBytesEqual(spentOutput.slice(9, 2), hex"5120"), "Spent output is not a P2TR output"); // OP_1 OP_PUSHBYTES32
        bytes memory pubKey = spentOutput.slice(11, 32);
        bytes4 sequence = payoutInput.extractSequenceLEWitness();
        bytes32 shaSingleOutput = sha256(abi.encodePacked(payoutOutput));

        // Construct the message hash for the P2TR signature according to BIP-341
        bytes memory message = abi.encodePacked(EPOCH, SIGHASH_SINGLE_ANYONECANPAY_HASH_TYPE, payoutTx.version, payoutTx.locktime, SPEND_TYPE_NO_EXT, spentTxId, spentIndex, spentOutput, sequence, shaSingleOutput);
        bytes32 messageHash = taggedHash("TapSighash", message);
        bytes memory signatureWithLen = payoutWitness.extractItemFromWitness(0);
        bytes memory signature = signatureWithLen.slice(1, signatureWithLen.length - 1);
        
        require(isSchnorrSigValid(pubKey, messageHash, signature), "Invalid signature");
        
        UTXO memory spentUtxo = UTXO({
            txId: spentTxId,
            outputId: spentIndex
        });
        emit SafeWithdrawal(payoutTx, spentUtxo, withdrawalUTXOs.length);

        withdraw(spentTxId, spentIndex);
```
