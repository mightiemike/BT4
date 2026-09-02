## Finding

### Title
`BitcoinLightClient.verifyInclusion` (and `verifyInclusionByTxId`) returns `true` for transactions that were never recorded, because unset block hashes default to `bytes32(0)` and collide with the legitimate zero-witness-root case for single-transaction blocks — ([File: crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol])

### Summary
This is the same bug class as the referenced Gearbox report: a state variable that is never explicitly initialized silently defaults to zero, and that zero value is treated by downstream logic as if it were a legitimate value rather than "unset." In Citrea's `BitcoinLightClient`, `witnessRoots[blockHash]` and `coinbaseDepths[blockHash]` for any block hash that has never been set via `setBlockInfo` default to `bytes32(0)` / `0`. The contract's own documentation explicitly acknowledges that `0` is *also* a legitimate witness root for a genuine single-transaction block [1](#0-0) , but this ambiguity is not just a documentation caveat for external integrators — it is directly exploitable against the contract's own `verifyInclusion` functions, which are public and unauthenticated.

### Finding Description
`verifyInclusion` and `verifyInclusionByTxId` compute a merkle-proof length check directly from `coinbaseDepths[_blockHash]`, and then verify inclusion against `witnessRoots[_blockHash]`: [2](#0-1) 

Both mappings default to zero for any `_blockHash` that has never been written by `setBlockInfo` — including a `_blockHash` value of `bytes32(0)` obtained from `blockHashes[_blockNumber]` when `_blockNumber` is a height the light client has not yet advanced to: [3](#0-2) 

`ValidateSPV.prove` (bitcoin-spv library) contains a well-known shortcut for the degenerate/single-leaf merkle case: if `_txid == _merkleRoot && _index == 0 && _intermediateNodes.length == 0`, it returns `true` without doing any hashing. Given the zero-defaults above, calling:

```
verifyInclusion(futureOrUnsetBlockNumber, bytes32(0), "", 0)
```

satisfies `require(_proof.length == coinbaseDepths[_blockHash] * HASH_LENGTH)` trivially (`0 == 0 * 32`), then hits `ValidateSPV.prove(0x00..0, 0x00..0, "", 0)`, which returns `true` — even though the referenced block has never been recorded by the light client at all.

This is the same root cause pattern as `expectedLiquidityLimit`: a variable that can legitimately be zero (`witnessRoots`/single-tx-block case) is never distinguished from "not yet initialized," and the contract's core security check (`ValidateSPV.prove`) does not gate against this collision.

### Impact Explanation
This breaks the DA/Bitcoin-inclusion binding the light client exists to enforce: "if `verifyInclusion` returns `true`, the wtxId was actually included in that Bitcoin block." Instead, an unprivileged caller can obtain a `true` result for a nonexistent transaction in a block that has not even been synced yet by the system caller, i.e. a forged DA inclusion result. This maps to the Critical category: "a forged DA inclusion or completeness result." Any contract or off-chain integrator on Citrea that trusts `BitcoinLightClient.verifyInclusion`/`verifyInclusionByTxId` as a source of truth about Bitcoin state (as the docs describe cross-chain applications doing) can be fooled into accepting a fabricated inclusion claim.

Note: the `Bridge.deposit` path itself is not directly exploitable this way because `wtxId` there is derived deterministically from the supplied `moveTx` via `WitnessUtils.calculateWtxId`, so an attacker cannot cheaply produce a `moveTx` whose wtxId hashes to exactly `bytes32(0)`. The exploitable surface is the public `verifyInclusion`/`verifyInclusionByTxId` functions themselves and any consumer that relies on them.

### Likelihood Explanation
High likelihood of exploitability for the specific call pattern: it requires no signatures, no privileged role, and no computational effort — just calling the public view functions with `_wtxId = bytes32(0)`, `_proof = ""`, `_index = 0`, and any `_blockNumber`/`_blockHash` that has not been recorded (trivial for future block numbers, which is the common case since the light client always lags Bitcoin tip).

### Recommendation
Do not rely on zero-valued mapping entries as meaningful state. Add an explicit `recorded`/`exists` mapping (or pack a sentinel/nonzero marker) set only inside `setBlockInfo`, and require it before accepting any `verifyInclusion`/`verifyInclusionByTxId` result. Additionally, guard `ValidateSPV.prove`'s shortcut path by rejecting calls where `_blockHash`/`witnessRoot` corresponds to an unset entry, e.g. `require(_blockNumber < blockNumber, "Block not yet recorded")` and `require(witnessRoots[_blockHash] != bytes32(0) || <block explicitly known to be single-coinbase>)`.

### Proof of Concept
```solidity
// bitcoinLightClient has synced blocks [0, currentTip)
uint256 futureBlockNumber = currentTip + 1000; // never set via setBlockInfo

bool included = bitcoinLightClient.verifyInclusion(
    futureBlockNumber,
    bytes32(0),   // fabricated wtxId
    "",           // empty proof
    0             // index 0
);
// included == true, despite `futureBlockNumber` never having been recorded
``` [4](#0-3)

### Citations

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L11-13)
```text
//  WARNING: Integrators must be aware of the following points:
// - Block hash getters returning 0 value means no such block is recorded
// - Witness root getters returning 0 value doesn't necessarily mean no such block is recorded, as 0 is also a valid witness root hash in the case of a 1 transaction block
```

**File:** crates/evm/src/evm/system_contracts/src/BitcoinLightClient.sol (L41-53)
```text
    /// @notice Can only be called after the initial block number is set
    /// @dev Block number is incremented by the contract as no block info should be overwritten or skipped
    /// @param _blockHash Hash of the current L1 block
    /// @param _witnessRoot Witness root of the current L1 block, must be in little endian 
    function setBlockInfo(bytes32 _blockHash, bytes32 _witnessRoot, uint256 _coinbaseDepth) external onlySystem {
        uint256 _blockNumber = blockNumber;
        require(_blockNumber != 0, "Not initialized");
        blockHashes[_blockNumber] = _blockHash;
        blockNumber = _blockNumber + 1;
        witnessRoots[_blockHash] = _witnessRoot;
        coinbaseDepths[_blockHash] = _coinbaseDepth;
        emit BlockInfoAdded(_blockNumber, _blockHash, _witnessRoot, _coinbaseDepth);
    }
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
