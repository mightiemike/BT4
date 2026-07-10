### Title
Relayer Depends on a Single Bitcoin Node Endpoint with No Cross-Validation, Enabling Fork-Chain Injection into the Light Client — (File: `relayer/src/config.rs`, `relayer/src/bitcoin_client.rs`, `relayer/src/main.rs`)

### Summary

The relayer service fetches all Bitcoin block headers from exactly one configured Bitcoin RPC endpoint. There is no mechanism to consult multiple nodes or cross-validate returned headers. If that single endpoint is compromised or serves headers from a minority fork, the relayer will faithfully submit those headers to the NEAR contract, which will accept them (they carry valid PoW), corrupt its canonical chain mapping, and subsequently return `true` from `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` for transactions that do not exist on the real Bitcoin mainchain.

---

### Finding Description

`BitcoinConfig` holds exactly one `endpoint: String` field with no provision for fallback or secondary nodes. [1](#0-0) 

`Client::new()` constructs a single `CustomMinreqHttpTransport` bound to that one URL and wraps it in a single `bitcoincore_rpc::Client`. [2](#0-1) 

`Synchronizer` holds a single `Arc<BitcoinClient>` and calls `get_block_count`, `get_block_hash`, and `get_aux_block_header` exclusively through it. [3](#0-2) 

The sync loop calls `self.bitcoin_client.get_block_count()` to determine the chain tip, then `fetch_blocks_to_submit` to retrieve headers — all from the same single source, with no secondary confirmation. [4](#0-3) 

`get_last_correct_block_height` also cross-checks NEAR's stored hashes against the Bitcoin node, but it uses the same single node for that cross-check, so a compromised node can satisfy both sides of the comparison. [5](#0-4) 

On the contract side, `submit_blocks` validates PoW but has no way to distinguish the real Bitcoin mainchain from a valid minority fork — it trusts whatever the relayer submits. [6](#0-5) 

---

### Impact Explanation

A compromised Bitcoin node can serve real, PoW-valid headers from a minority fork. The relayer submits them; the contract accepts them and updates `mainchain_tip_blockhash` and `mainchain_height_to_header` to reflect the fork. Any downstream NEAR contract that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will receive `true` for transactions that exist only on the fork and not on the real Bitcoin mainchain. If those downstream contracts gate fund releases on SPV proof results (the stated purpose of the light client), an attacker who controls the Bitcoin node can fabricate "confirmed" Bitcoin deposits and drain the downstream contract. [7](#0-6) 

---

### Likelihood Explanation

The example mainnet config ships with a public NEAR RPC endpoint, suggesting operators are expected to use publicly accessible infrastructure. Operators who point the relayer at a third-party Bitcoin RPC provider (e.g., a hosted node service) face the same risk as the original Polkaswap report: that provider is a single point of failure and a single point of attack. The `btc_mainnet.toml` config file provides no guidance to use a self-hosted node or multiple nodes. [8](#0-7) 

---

### Recommendation

- Extend `BitcoinConfig` to accept a list of endpoints (`endpoints: Vec<String>`).
- In `Client` or a new `MultiClient` wrapper, fetch headers from all configured nodes and require a quorum (e.g., majority agreement on block hash at each height) before accepting a header.
- Require at least one self-hosted Bitcoin full node among the configured sources.
- Document this requirement in the relayer README and config example files.

---

### Proof of Concept

1. Operator configures the relayer with `bitcoin.endpoint = "https://attacker-controlled-node.example.com"` (or attacker compromises the operator's existing node).
2. Attacker's node returns headers from a minority fork at heights N, N+1, … that have valid PoW (real Bitcoin headers from a stale fork).
3. Relayer calls `get_block_count()` → attacker returns a height matching the fork tip.
4. Relayer calls `get_block_hash(height)` and `get_aux_block_header(hash)` → attacker returns fork headers.
5. `fetch_blocks_to_submit` assembles the fork headers; `prepare_and_submit_batches` signs and submits them to the NEAR contract.
6. Contract's `submit_block_header` validates PoW (passes — these are real Bitcoin headers), updates `mainchain_tip_blockhash` to the fork tip, and writes fork hashes into `mainchain_height_to_header`.
7. Attacker calls `verify_transaction_inclusion` with a transaction that exists only on the fork. Contract returns `true`.
8. Downstream contract releases funds. [9](#0-8) [10](#0-9)

### Citations

**File:** relayer/src/config.rs (L29-35)
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BitcoinConfig {
    pub endpoint: String,
    pub node_user: Option<String>,
    pub node_password: Option<String>,
    pub node_headers: Option<Vec<(String, String)>>,
}
```

**File:** relayer/src/bitcoin_client.rs (L108-128)
```rust
    pub fn new(config: &Config) -> Self {
        let config = config.bitcoin.clone();

        let basic_auth = match (config.node_user, config.node_password) {
            (Some(user), Some(password)) => {
                Some(CustomMinreqHttpTransport::basic_auth(user, Some(&password)))
            }
            _ => None,
        };

        let client = CustomMinreqHttpTransport {
            url: config.endpoint,
            timeout: std::time::Duration::from_secs(15),
            basic_auth,
            headers: config.node_headers.unwrap_or_default(),
        };

        let inner = bitcoincore_rpc::Client::from_jsonrpc(client.into());

        Self { inner }
    }
```

**File:** relayer/src/main.rs (L23-28)
```rust
struct Synchronizer {
    bitcoin_client: Arc<BitcoinClient>,
    near_client: NearClient,
    config: Config,
    batch_sizer: Mutex<AdaptiveBatchSizer>,
}
```

**File:** relayer/src/main.rs (L75-120)
```rust
    async fn fetch_blocks_to_submit(
        &self,
        start_height: u64,
        end_height: u64,
    ) -> Vec<(u64, btc_types::header::Header, Option<AuxData>)> {
        let mut handles = Vec::new();
        for current_height in start_height..=end_height {
            handles.push(tokio::spawn({
                let bitcoin_client = self.bitcoin_client.clone();
                async move { get_block_header(&bitcoin_client, current_height) }
            }));
        }

        let mut blocks = Vec::new();
        let mut min_failed_height = None;

        for handler in handles {
            match handler.await {
                Ok(Ok((height, block_header, aux_data))) => {
                    blocks.push((height, block_header, aux_data));
                }
                Ok(Err(current_height)) => {
                    warn!("Failed to process block at height {current_height}");
                    min_failed_height = Some(
                        min_failed_height
                            .map_or(current_height, |min: u64| min.min(current_height)),
                    );
                }
                Err(e) => {
                    warn!("Task failed with error: {e:?}");
                    tokio::time::sleep(std::time::Duration::from_secs(
                        self.config.sleep_time_on_fail_sec,
                    ))
                    .await;
                    break;
                }
            }
        }

        blocks.sort_by_key(|(height, _, _)| *height);
        if let Some(min_failed_height) = min_failed_height {
            blocks.retain(|(height, _, _)| *height < min_failed_height);
        }

        blocks
    }
```

**File:** relayer/src/main.rs (L239-250)
```rust
            let latest_height = continue_on_fail!(
                self.bitcoin_client.get_block_count(),
                "Bitcoin Client: Error on get_block_count",
                self.config.sleep_time_on_fail_sec,
                'main_loop
            );

            let start_height =
                first_block_height_to_submit.load(std::sync::atomic::Ordering::Relaxed);
            let end_height = latest_height.min(start_height.saturating_add(current_fetch_size));

            let blocks_to_submit = self.fetch_blocks_to_submit(start_height, end_height).await;
```

**File:** relayer/src/main.rs (L296-335)
```rust
    async fn get_last_correct_block_height(
        &self,
    ) -> Result<u64, Box<dyn std::error::Error + Send + Sync>> {
        let last_block_header = self.near_client.get_last_block_header().await?;
        let last_block_height = last_block_header.block_height;
        if self.get_bitcoin_block_hash_by_height(last_block_height)?
            == last_block_header.block_hash.to_string()
        {
            return Ok(last_block_height);
        }
        let last_block_hashes_in_relay_contract = self
            .near_client
            .get_last_n_blocks_hashes(self.config.max_fork_len, 1)
            .await?;

        let last_block_hashes_count = last_block_hashes_in_relay_contract.len();

        let mut height: u64 = last_block_height - 1;

        for i in 0..last_block_hashes_count {
            if last_block_hashes_in_relay_contract[last_block_hashes_count - i - 1]
                == self.get_bitcoin_block_hash_by_height(height)?
            {
                return Ok(height);
            }

            height -= 1;
        }

        Err("The block Height not found".into())
    }

    fn get_bitcoin_block_hash_by_height(
        &self,
        height: u64,
    ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        let block_from_bitcoin_node = self.bitcoin_client.get_block_header_by_height(height)?;

        Ok(block_from_bitcoin_node.block_hash().to_string())
    }
```

**File:** contract/src/lib.rs (L169-198)
```rust
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L488-528)
```rust
    #[cfg(not(feature = "dogecoin"))]
    #[allow(clippy::needless_pass_by_value)]
    fn submit_block_header(&mut self, header: Header, skip_pow_verification: bool) {
        // We do not have a previous block in the headers_pool, there is a high probability
        // it means we are starting to receive a new fork,
        // so what we do now is we are returning the error code
        // to ask the relay to deploy the previous block.
        //
        // Offchain relay now, should submit blocks one by one in decreasing height order
        // 80 -> 79 -> 78 -> ...
        // And do it until we can accept the block.
        // It means we found an initial fork position.
        // We are starting to gather new fork from this initial position.
        #[allow(clippy::useless_conversion)]
        let prev_block_header = self.get_prev_header(&header.clone().into());
        let current_block_hash = header.block_hash();

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }

        self.submit_block_header_inner(current_header, &prev_block_header);
```

**File:** relayer/configs/btc_mainnet.toml (L1-12)
```text
max_fork_len = 500
sleep_time_on_fail_sec = 30
sleep_time_on_reach_last_block_sec = 60
sleep_time_after_sync_iteration_sec = 5
fetch_batch_size = 150
submit_batch_size = 15
min_batch_size = 1

[near]
endpoint = "https://rpc.mainnet.near.org"
btc_light_client_account_id = "btc-client.bridge.near"
transaction_timeout_sec = 120
```
