### Title
Unset-block inclusion check in `BitcoinLightClient.verifyInclusion` collapses to default-value comparison instead of reverting - (File: crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol)

### Summary
This is the same bug class as the Chainlink `latestAnswer` finding: a getter/verification path silently returns a "zero/default" result for missing data instead of reverting, and a caller that doesn't defend against that default can be fooled into treating "no data" as "valid data." In `BitcoinLightClient.sol`, `verifyInclusion(uint256 _blockNumber, ...)` and `getWitnessRootByNumber`/`getBlockHash` return the Solidity default (`bytes32(0)`) for any block number that has not yet been recorded, rather than reverting, and this default flows into inclusion-proof verification and, downstream, into `Bridge.sol`'s `deposit`/`safeWithdraw` flow.

### Finding Description
`blockHashes[_blockNumber]` and `witnessRoots[_blockHash]` are plain mappings with no explicit "exists" flag; an unset entry reads as `bytes32(0)` [1](#0-0) . The contract's own comment acknowledges this ambiguity risk explicitly ("Witness root getters returning 0 value doesn't necessarily mean no such block is recorded") [2](#0-1) .

`verifyInclusion(uint256 _blockNumber, ...)` looks up `blockHashes[_blockNumber]` and passes the (possibly-zero) hash straight into `_verifyInclusion` without checking that the block was actually initialized: [3](#0-2) . Inside `_verifyInclusion`, the required proof length is derived from `coinbaseDepths[_blockHash]`, which is also `0` by default for an unset block, so the length check `require(_proof.length == coinbaseDepths[_blockHash] * HASH_LENGTH)` is trivially satisfied by an empty proof array, and `witnessRoots[_blockHash]` resolves to `0` as well [4](#0-3) .

`Bridge.sol`'s `validateAndCheckInclusion` calls exactly this uint256-indexed `verifyInclusion` with the caller-supplied `proof.blockHeight`, trusting a `true` result as proof the move transaction is included in a real Bitcoin block: [5](#0-4) . This function is invoked both by the system caller and by the (semi-trusted but non-key-holding) `operator` role in `deposit`, since `deposit` carries the `onlySystemOrOperator` modifier [6](#0-5) .

Whether the "all-zero" degenerate case (`_blockHash == 0`, empty `_proof`, `_index == 0`) is actually accepted by the vendored `ValidateSPV.prove` routine could not be confirmed: the `bitcoin-spv` library source is an external dependency and its `.sol` file is not present/indexed in this repository (only import statements were found in `BitcoinLightClient.sol`, `Bridge.sol`, and `WitnessUtils.sol`) [7](#0-6) . Per the scan rules, third-party crate defects with no path through this repository are out of scope, so I cannot confirm that this specific path lets `ValidateSPV.prove` return `true` for the degenerate zero/empty inputs — that would need to be proven against the actual `bitcoin-spv` implementation, which is not visible in this codebase's index.

### Impact Explanation
If the degenerate zero-hash/empty-proof/zero-index case *is* accepted by `ValidateSPV.prove` (unverifiable from this repo's contents), an attacker or a malicious `operator` could reference a not-yet-recorded `blockHeight` in `proof.blockHeight` and satisfy `verifyInclusion` without any real Bitcoin transaction ever existing, breaking the binding "cBTC credited == Bitcoin deposit that actually happened," which is exactly the Critical-severity category this scan targets. Absent confirmation of the underlying library's behavior, this cannot be asserted as a proven exploit path — it is only a structural weakness (zero-value ambiguity in the light client's storage) analogous to the `latestAnswer` deprecation bug, not a demonstrated forged-inclusion vulnerability.

### Likelihood Explanation
Low/unproven: the length-of-proof check happening to be satisfiable by zero (`coinbaseDepths` defaulting to 0) is a real code-level fact in this repo, but whether `ValidateSPV.prove` treats a zero-length proof + zero root + zero txid/wtxid as a valid inclusion proof depends entirely on the external `bitcoin-spv` library, which this scan is not permitted to assume defects in.

### Recommendation
Add an explicit existence check before proof verification, e.g. `require(_blockHash != bytes32(0), "Unknown block")` in `verifyInclusion(uint256, ...)`, and/or use a separate "initialized" bitmap for `blockHashes`/`coinbaseDepths` rather than relying on the Solidity zero-default. This mirrors the fix recommended for the Chainlink report: never let an "unset/no-answer" default silently pass validation logic that expects a genuine value.

### Proof of Concept
Not constructible with certainty from this repository alone, because the decisive step (whether `ValidateSPV.prove` accepts the degenerate zero-length-proof case) lives in the external, unindexed `bitcoin-spv` dependency. Conceptually: call `Bridge.deposit` (or trigger `verifyInclusion` directly) with `proof.blockHeight` set to a Bitcoin height beyond the current `blockNumber` (never written by `setBlockInfo`) and `proof.intermediateNodes` set to an empty byte array — this satisfies the `require(_proof.length == coinbaseDepths[_blockHash] * HASH_LENGTH)` check in `_verifyInclusion` trivially, and the outcome then hinges entirely on the external library's handling of `witnessRoot == 0`.

### Citations

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L5-6)
```text
import "bitcoin-spv/solidity/contracts/ValidateSPV.sol";
import "bitcoin-spv/solidity/contracts/BTCUtils.sol";
```

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L11-13)
```text
//  WARNING: Integrators must be aware of the following points:
// - Block hash getters returning 0 value means no such block is recorded
// - Witness root getters returning 0 value doesn't necessarily mean no such block is recorded, as 0 is also a valid witness root hash in the case of a 1 transaction block
```

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L21-24)
```text
    uint256 public blockNumber;
    mapping(uint256 => bytes32) public blockHashes;
    mapping(bytes32 => bytes32) public witnessRoots;
    mapping(bytes32 => uint256) public coinbaseDepths;
```

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L91-93)
```text
    function verifyInclusion(uint256 _blockNumber, bytes32 _wtxId, bytes calldata _proof, uint256 _index) external view returns (bool) {
        return _verifyInclusion(blockHashes[_blockNumber], _wtxId, _proof, _index);
    }
```

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L111-115)
```text
    function _verifyInclusion(bytes32 _blockHash, bytes32 _wtxId, bytes calldata _proof, uint256 _index) internal view returns (bool) {
        require(_proof.length == coinbaseDepths[_blockHash] * HASH_LENGTH, "Invalid proof length");
        bytes32 _witnessRoot = witnessRoots[_blockHash];
        return ValidateSPV.prove(_wtxId, _witnessRoot, _proof, _index);
    }
```

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L186-197)
```text
    function deposit(
        Transaction calldata moveTx,
        MerkleProof calldata proof,
        bytes32 shaScriptPubkeys
    ) external onlySystemOrOperator whenNotPaused {
        // We don't need to check if the contract is initialized, as without an `initialize` call and `deposit` calls afterwards,
        // only the system caller can execute a transaction on Citrea, as no addresses have any balance. Thus there's no risk of 
        // `deposit` being called before `initialize` maliciously.

        // Validate that the move transaction is properly formatted and is included in a Bitcoin block
        (bytes32 wtxId, uint256 nIns) = validateAndCheckInclusion(moveTx, proof);
        require(nIns == 1, "Only one input allowed");
```

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L416-428)
```text
    function validateAndCheckInclusion(Transaction calldata txn, MerkleProof calldata proof) internal view returns (bytes32, uint256) {
        bytes32 wtxId = WitnessUtils.calculateWtxId(txn.version, txn.flag, txn.vin, txn.vout, txn.witness, txn.locktime);
        require(BTCUtils.validateVin(txn.vin), "Vin is not properly formatted");
        require(BTCUtils.validateVout(txn.vout), "Vout is not properly formatted");
        
        (, uint256 nIns) = BTCUtils.parseVarInt(txn.vin);
        // Number of inputs == number of witnesses
        require(WitnessUtils.validateWitness(txn.witness, nIns), "Witness is not properly formatted");

        require(LIGHT_CLIENT.verifyInclusion(proof.blockHeight, wtxId, proof.intermediateNodes, proof.index), "Transaction is not in block");

        return (wtxId, nIns);
    }
```
