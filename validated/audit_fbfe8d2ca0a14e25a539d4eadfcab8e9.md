### Title
Basic HTTP Authorization Credential Exposure Without TLS in Bitcoin RPC Client - (File: relayer/src/bitcoin_client.rs)

### Summary

The relayer's `CustomMinreqHttpTransport` sends Bitcoin RPC credentials as a `Basic` Authorization header over a plain HTTP connection. The canonical configuration files ship with `http://` endpoints. Any network-path attacker can trivially decode the base64 credential and subsequently perform a man-in-the-middle attack, feeding the relayer fraudulent block headers that are then submitted to and accepted by the NEAR light client contract, corrupting its canonical chain state.

### Finding Description

`CustomMinreqHttpTransport::basic_auth()` constructs a `Basic <base64(user:pass)>` string: [1](#0-0) 

This string is attached as the `Authorization` header on every JSON-RPC POST request: [2](#0-1) 

The `url` field is taken directly from `config.bitcoin.endpoint` with no scheme validation or TLS enforcement: [3](#0-2) 

`Config::validate()` checks that the endpoint is non-empty but never verifies it begins with `https://`: [4](#0-3) 

The canonical example configuration and the shipped production config files explicitly use a plain `http://` scheme: [5](#0-4) 

Base64 encoding provides zero confidentiality; the credential is recoverable with a single `base64 -d` call on any captured packet.

### Impact Explanation

**Severity: Medium | Impact: High | Likelihood: Low**

A network-path attacker (e.g., on the same LAN, a compromised router, or a cloud provider with packet inspection) who captures a single relayer-to-bitcoind request obtains the plaintext RPC username and password. With those credentials the attacker can:

1. **Credential theft** — authenticate directly to the Bitcoin node and issue arbitrary RPC commands (wallet operations, mempool manipulation, etc.).
2. **MITM of block data** — intercept and replace `getblockheader` responses with crafted headers. The relayer forwards these to `submit_blocks` on the NEAR contract. If the forged headers pass PoW validation (e.g., by replaying real headers at a different height), the contract's canonical chain mapping is corrupted, causing `verify_transaction_inclusion` to return incorrect results for downstream NEAR contracts that rely on SPV proofs.

The second impact directly corrupts the trust model of the light client: a recipient NEAR contract that calls `verify_transaction_inclusion` may accept a proof for a transaction that was never confirmed on the real Bitcoin chain.

### Likelihood Explanation

The relayer is an off-chain service that must reach a Bitcoin node, often over a network segment that is not fully controlled (cloud VPC, Docker bridge, remote RPC provider). The shipped configs default to `http://`, making this the path of least resistance for any operator following the provided examples. An attacker with passive network access to that segment can capture credentials without active interaction.

### Recommendation

1. **Enforce HTTPS in `Config::validate()`**: reject any `bitcoin.endpoint` that does not begin with `https://`.
2. **Add a scheme check in `CustomMinreqHttpTransport::new()`**: panic or return an error if the URL scheme is not `https` when `basic_auth` is `Some`.
3. **Update all shipped config files** (`config.toml.example`, `relayer/configs/btc_mainnet.toml`, `relayer/configs/btc_testnet.toml`) to use `https://` endpoints.
4. If the Bitcoin node is co-located on the same host (loopback only), document that assumption explicitly and add a check that the endpoint resolves to `127.0.0.1`/`::1` before permitting `http://`.

### Proof of Concept

```
# 1. Attacker passively captures traffic on the relayer-to-bitcoind path:
tcpdump -i eth0 -A 'tcp port 8332' 2>/dev/null | grep -i authorization

# Captured header (example):
# Authorization: Basic Yml0Y29pbjpiaXRjb2lu

# 2. Decode credential:
echo "Yml0Y29pbjpiaXRjb2lu" | base64 -d
# Output: bitcoin:bitcoin

# 3. Attacker authenticates directly to the Bitcoin node:
curl -u bitcoin:bitcoin http://<bitcoind-ip>:8332/ \
  -d '{"method":"getblockcount","params":[],"id":1}'

# 4. For MITM: attacker intercepts getblockheader responses and replaces
#    them with headers from a different height or a crafted fork.
#    The relayer calls submit_blocks() with the poisoned headers.
#    The NEAR contract stores the attacker-chosen chain tip as canonical,
#    causing verify_transaction_inclusion() to validate proofs against
#    a fraudulent chain state.
```

The root cause is in `relayer/src/bitcoin_client.rs` lines 43–48 (credential transmission) and `relayer/config.toml.example` line 10 (plain-HTTP default), with no mitigating check in `relayer/src/config.rs` lines 122–128.

### Citations

**File:** relayer/src/bitcoin_client.rs (L43-48)
```rust
        let req = match &self.basic_auth {
            Some(auth) => minreq::Request::new(minreq::Method::Post, &self.url)
                .with_timeout(self.timeout.as_secs())
                .with_header("Authorization", auth)
                .with_headers(self.headers.clone())
                .with_json(&req)?,
```

**File:** relayer/src/bitcoin_client.rs (L73-80)
```rust
    pub fn basic_auth(user: String, pass: Option<&str>) -> String {
        let mut s = user;
        s.push(':');
        if let Some(ref pass) = pass {
            s.push_str(pass.as_ref());
        }
        format!("Basic {}", &jsonrpc::base64::encode(s.as_bytes()))
    }
```

**File:** relayer/src/bitcoin_client.rs (L118-123)
```rust
        let client = CustomMinreqHttpTransport {
            url: config.endpoint,
            timeout: std::time::Duration::from_secs(15),
            basic_auth,
            headers: config.node_headers.unwrap_or_default(),
        };
```

**File:** relayer/src/config.rs (L122-128)
```rust
    fn validate(&self) -> Result<()> {
        let mut missing = Vec::new();

        // Bitcoin node connection is required
        if self.bitcoin.endpoint.is_empty() {
            missing.push("RELAYER_BITCOIN_ENDPOINT (Bitcoin node RPC endpoint)");
        }
```

**File:** relayer/config.toml.example (L9-12)
```text
[bitcoin]
endpoint = "http://bitcoind:8332"
node_user = "bitcoin"
node_password = "bitcoin"
```
