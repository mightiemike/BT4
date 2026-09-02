[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/bitcoin-da/src/tx_signer.rs (L39-50)
```rust
    pub fn into_txs_with_id(self) -> [TxWithId; 2] {
        [
            TxWithId {
                tx: self.commit.tx,
                id: self.commit.id,
            },
            TxWithId {
                tx: self.reveal.tx,
                id: self.reveal.id,
            },
        ]
    }
```

**File:** crates/bitcoin-da/src/tx_signer.rs (L134-148)
```rust
        let serialized_reveal_tx = encode::serialize(&reveal.tx);
        Ok(SignedTxPair {
            commit: SignedTxWithId {
                hex: signed_raw_commit_tx.hex,
                id: commit.compute_txid(),
                tx: commit,
            },
            reveal: SignedTxWithId {
                hex: serialized_reveal_tx,
                id: reveal.id,
                tx: reveal.tx,
            },
            kind,
        })
    }
```

**File:** crates/bitcoin-da/src/tx_signer.rs (L210-225)
```rust

            let serialized_reveal_tx = encode::serialize(&reveal);
            raw_txs.push(SignedTxPair {
                commit: SignedTxWithId {
                    hex: signed_raw_commit_tx.hex,
                    id: commit.compute_txid(),
                    tx: commit,
                },
                reveal: SignedTxWithId {
                    hex: serialized_reveal_tx,
                    id: reveal.compute_txid(),
                    tx: reveal,
                },
                kind: TransactionKind::Chunks,
            });
        }
```

**File:** crates/bitcoin-da/src/tx_signer.rs (L253-267)
```rust
        let serialized_reveal_tx = encode::serialize(&reveal.tx);

        raw_txs.push(SignedTxPair {
            commit: SignedTxWithId {
                hex: signed_raw_commit_tx.hex,
                id: commit.compute_txid(),
                tx: commit,
            },
            reveal: SignedTxWithId {
                hex: serialized_reveal_tx,
                id: reveal.id,
                tx: reveal.tx,
            },
            kind: TransactionKind::Aggregate,
        });
```
