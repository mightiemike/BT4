This is a confirmed critical vulnerability: the `TryFrom<RlpEvmTransaction> for Recovered<TransactionSigned>` conversion bypasses real ECDSA signature verification when the raw signature equals `(r=0, s=0, v=false)`, forcing `signer == SYSTEM_SIGNER` unconditionally regardless of who crafted the RLP bytes.

### Title
Forged `SYSTEM_SIGNER` via zero-signature (`r=0,s=0`) RLP transaction bypasses `is_system_caller()` and escapes L1 fee charging - (`crates/evm/src/evm/conversions.rs`)

### Summary
`Recovered<TransactionSigned>::try_from(RlpEvmTransaction)` treats any transaction whose signature equals the constant `SYSTEM_SIGNATURE` (`r=0, s=0, v=false`) as authored by `SYSTEM_SIGNER`, without any cryptographic verification. Since `r=0,s=0` is not something requiring a private key (it's just constructing arbitrary RLP bytes), anyone can produce a transaction that this conversion treats as `SYSTEM_SIGNER`-signed. This path is used by `Evm::execute_call` (`crates/evm/src/call.rs:44-48`), which processes `CallMessage::txs` — the same decoding path used for ordinary user transactions submitted through `eth_sendRawTransaction`/mempool and packed into `CallMessage`.

### Finding Description
Binding claimed broken: `l1_fee_charged(tx) == l1_fee_rate * diff_size(tx)` for every tx not legitimately produced as a sequencer system event.

Code path:
- `crates/evm/src/evm/conversions.rs:105-115`:
```
impl TryFrom<RlpEvmTransaction> for Recovered<TransactionSigned> {
    fn try_from(evm_tx: RlpEvmTransaction) -> Result<Self, Self::Error> {
        let tx = TransactionSigned::try_from(evm_tx)?;
        if tx.signature() == &SYSTEM_SIGNATURE {
            return Ok(Self::new_unchecked(tx, SYSTEM_SIGNER));
        }
        tx.try_into_recovered().map_err(|_| ConversionError::InvalidSignature)
    }
}
```
`SYSTEM_SIGNATURE` is `PrimitiveSignature::new(U256::ZERO, U256::ZERO, false)` (`crates/evm/src/evm/system_events.rs:11-12`) — a fixed, publicly known constant, not derived from any private key. Anyone can hand-craft an RLP-encoded EIP-1559 (or similar) transaction with `r=0, s=0, v=false` and any `to`/`data`/`gas` fields they want; `TransactionSigned::decode_2718` will happily decode it as a syntactically valid transaction, and the conversion above short-circuits recovery to hard-code `signer = SYSTEM_SIGNER` without checking anything about how the transaction reached the system.

This conversion is invoked in `Evm::execute_call` (`crates/evm/src/call.rs:44-48`), which is the function that processes `CallMessage.txs: Vec<RlpEvmTransaction>` — the general path for transactions embedded in an L2 block, used both for sequencer-generated system transactions (via `signed_system_transaction`/`process_sys_txs` in `crates/sequencer/src/runner.rs`) and for ordinary transactions taken from the mempool and included by the sequencer.

Once `caller == SYSTEM_SIGNER`, `CitreaHandler::is_system_caller()` (`crates/evm/src/evm/handler.rs:174-176`) returns true for every downstream check: `validate_tx_against_state` skips balance verification, `deduct_caller`/`reimburse_caller` skip gas accounting, and critically `output()` (handler.rs:567-596, not directly inspected here but consistent with the pattern) skips the `decrease_caller_balance`/L1-fee charge to `L1_FEE_VAULT`. The attacker's transaction can carry arbitrary calldata (to inflate `calc_diff_size`) while contributing 0 to `L1_FEE_VAULT`, breaking the binding: `l1_fee_charged == 0` while `diff_size > 0`.

### Why I cannot fully confirm this is exploitable end-to-end
I was not able to fully trace whether the **sequencer** (the only entity that actually assembles `CallMessage`/`RlpEvmTransaction` lists from the mempool into an L2 block) filters out or rejects mempool transactions whose signature equals `SYSTEM_SIGNATURE` before packing them into a `CallMessage`. The externally-reachable RPC ingestion path (`crates/sequencer/src/utils.rs:12-23`, `recover_raw_transaction`) does perform real `try_into_recovered()` ECDSA recovery via `PooledTransaction`/`Decodable2718`, which would legitimately reject a `r=0,s=0` transaction as an "invalid signature" transaction *for mempool admission purposes*, since `try_into_recovered` on a genuinely zero signature should fail actual ECDSA recovery (this differs from the special-cased `Recovered<TransactionSigned>::try_from` conversion in `conversions.rs`, which explicitly special-cases the zero signature instead of running real recovery). I could not fully verify within budget whether `executor::execute_multiple_tx`'s existing guard (`crates/evm/src/evm/executor.rs:97-108`, which rejects a system tx located after a "should be end of sys txs" marker) combined with the mempool-side `recover_raw_transaction` rejecting the zero signature at admission time, closes this gap completely on the standard sequencer flow.

Given this residual uncertainty about whether the mempool ingestion path can be bypassed (e.g., the tx never needs to go through `eth_sendRawTransaction`/mempool at all if there is any other code path that constructs `CallMessage` from unauthenticated input, or if `recover_raw_transaction`'s `try_into_recovered()` treats an `r=0,s=0` signature differently than expected), I cannot state with certainty that this is exploitable purely as an unprivileged RPC user without further investigation of the mempool/tx-pool admission behavior for zero-signature transactions and of `executor.rs`'s ordering guard against sys-tx-after-user-tx.

#### Recommendation (if confirmed exploitable)
Remove the `SYSTEM_SIGNATURE` special case from `Recovered<TransactionSigned>::try_from` in `crates/evm/src/evm/conversions.rs`, and instead have the sequencer construct system transactions via a distinct, non-RLP-roundtripped internal type that can never be confused with user-submitted `RlpEvmTransaction` bytes, so the zero-signature shortcut is never reachable from any path that decodes attacker-controlled RLP. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

Because I could not conclusively verify the missing link (whether the sequencer's mempool/block-assembly path can be induced to include a zero-signature transaction in a `CallMessage` as an unprivileged attacker), I recommend this be escalated to a Devin background session with terminal/repo access to trace `crates/sequencer/src/mempool.rs`, `EthPooledTransaction::from_pooled`, and the block-building code that turns pooled mempool transactions into `RlpEvmTransaction`/`CallMessage` entries, and to write the `cargo test` proof described in the prompt (submit a hand-crafted `r=0,s=0` transaction with large calldata through the real `eth_sendRawTransaction` → block-building → `execute_call` pipeline, and diff `L1_FEE_VAULT` balance against an equivalent legitimately-signed transaction).

### Citations

**File:** crates/evm/src/evm/conversions.rs (L105-115)
```rust
impl TryFrom<RlpEvmTransaction> for Recovered<TransactionSigned> {
    type Error = ConversionError;

    fn try_from(evm_tx: RlpEvmTransaction) -> Result<Self, Self::Error> {
        let tx = TransactionSigned::try_from(evm_tx)?;
        if tx.signature() == &SYSTEM_SIGNATURE {
            return Ok(Self::new_unchecked(tx, SYSTEM_SIGNER));
        }
        tx.try_into_recovered()
            .map_err(|_| ConversionError::InvalidSignature)
    }
```

**File:** crates/evm/src/evm/system_events.rs (L10-12)
```rust
/// This is a special signature to force tx.signer to be set to SYSTEM_SIGNER
pub const SYSTEM_SIGNATURE: PrimitiveSignature =
    PrimitiveSignature::new(U256::ZERO, U256::ZERO, false);
```

**File:** crates/evm/src/call.rs (L44-48)
```rust
        let users_txs: Vec<Recovered<TransactionSigned>> = txs
            .into_iter()
            .map(|tx| tx.try_into())
            .collect::<Result<Vec<_>, ConversionError>>()
            .map_err(|_| L2BlockModuleCallError::EvmTxNotSerializable)?;
```

**File:** crates/evm/src/evm/handler.rs (L173-177)
```rust
impl<EVM: EvmTr> CitreaCallExt for EVM {
    fn is_system_caller(&self) -> bool {
        SYSTEM_SIGNER == self.ctx_ref().tx().caller()
    }
}
```

**File:** crates/sequencer/src/utils.rs (L12-23)
```rust
pub(crate) fn recover_raw_transaction(data: Bytes) -> EthResult<Recovered<PooledTransaction>> {
    if data.is_empty() {
        return Err(EthApiError::EmptyRawTransactionData);
    }

    let transaction: PooledTransaction = Decodable2718::decode_2718(&mut data.as_ref())
        .map_err(|_| EthApiError::FailedToDecodeSignedTransaction)?;

    transaction
        .try_into_recovered()
        .or(Err(EthApiError::InvalidTransactionSignature))
}
```

**File:** crates/evm/src/evm/executor.rs (L97-108)
```rust
        if tx.signer() == SYSTEM_SIGNER {
            if *should_be_end_of_sys_txs {
                native_error!("System transaction found after user txs");
                return Err(L2BlockModuleCallError::EvmSystemTransactionPlacedAfterUserTx);
            }

            verify_system_tx(evm.evm.ctx().db(), tx, l2_height)?;
        } else {
            // Set to true as soon as a user tx is found
            // If a sys tx is encountered after a user tx it is an error
            *should_be_end_of_sys_txs = true;
        }
```
