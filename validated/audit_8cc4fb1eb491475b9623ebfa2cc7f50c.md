### Title
Node's `functionsConnectorHandler` gates secret storage using a stale, periodically-synced subscription balance instead of live on-chain balance - ([File: core/services/functions/connector_handler.go])

### Summary
This is a valid analog of the Aloe `_getLiabilities()` bug class: a privileged authorization/paywall check is performed against a **stored (outdated)** snapshot of an on-chain value rather than the live current value, letting an unprivileged user exploit the sync window to bypass the check.

### Finding Description
The Functions gateway node handler gates `MethodSecretsSet` (writing user secrets into the DON's S4 storage) on a minimum subscription balance check: [1](#0-0) 

The balance used, `h.subscriptions.GetMaxUserBalance(fromAddr)`, is **not** read live from the chain at request time. It is served from an in-memory mirror (`onchainSubscriptions`/`userSubscriptions`) that is refreshed asynchronously on a fixed polling interval (`UpdateFrequencySec`, default `30` seconds per `core/scripts/functions/templates/oracle.toml`): [2](#0-1) [3](#0-2) 

The lookup itself is a simple in-memory map read against whatever the last poll cycle stored: [4](#0-3) 

This is structurally identical to the Aloe bug: a security-relevant check (there: account health/liability; here: subscription solvency/authorization to store secrets) is performed against a snapshot value ("stored" balance) instead of the true current value, and the snapshot is only refreshed periodically/eventually rather than atomically with the state it protects.

### Impact Explanation
An unprivileged Functions subscription owner can:
1. Fund a subscription so `GetMaxUserBalance` returns a value ≥ `minimumBalance` (`h.minimumBalance`) at cache-refresh time.
2. Withdraw/drain the subscription's on-chain LINK balance on the `FunctionsRouter` contract (an action fully within the owner's control and unprivileged).
3. Before the next `queryLoop` refresh (up to `UpdateFrequencySec` seconds, and potentially longer under high subscription-count churn since the loop processes subscriptions in bounded `UpdateRangeSize` batches per tick — see the `start`/`lastKnownCount` batching logic), continue sending `MethodSecretsSet` requests, which will pass the balance check using the stale cached value even though the account is now actually insolvent.

This allows an unfunded/insolvent user to keep writing secrets to the DON's S4 storage (a paid, resource-consuming service gated specifically to prevent free-riding/abuse), i.e., a bypass of a billing/authorization trust boundary that protects a privileged node resource (secret storage capacity, bandwidth, DON compute). It does not directly cause fund loss on its own, but it defeats the intended economic/anti-abuse gate in exactly the same "stale snapshot instead of live truth" manner as the referenced report, and is reachable by a normal unprivileged external caller through the gateway JSON-RPC interface — matching the required "unauthorized privileged node action" / "unsafe workflow execution" bar via bypass of the billing/authorization gate for a privileged storage action.

### Likelihood Explanation
High likelihood of reachability: the check is on the hot path of every `MethodSecretsSet` gateway request (`HandleGatewayMessage`), requires no special privileges beyond owning/having owned a Functions subscription, and the staleness window is deterministic and configurable by the node operator but non-zero by design (polling-based sync, batched over multiple ticks for larger subscription registries). No malicious node/operator/peer is required — the exploiting party is simply the subscription owner (an ordinary external, unprivileged user of the Functions/gateway system).

### Recommendation
Do not gate `MethodSecretsSet` (or any similar privileged/paid action) purely on the locally cached `OnchainSubscriptions` mirror. Either:
- Perform a live/`Pending: false, BlockNumber: latest` on-chain read of the subscription balance at request time (accepting added latency), or
- Bound the staleness explicitly and reject requests when the cached snapshot's last-updated block/timestamp exceeds a safety threshold, or
- Re-validate balance transactionally at the point of actual resource consumption/fulfillment rather than only at admission time, mirroring the Aloe fix of always deriving liabilities/balances from the up-to-date (accrued) state instead of the last-stored value.

### Proof of Concept
Not directly reproducible as a Solidity PoC (this is Go node/off-chain infrastructure), but the exploit path is deterministic given the code above:
1. Node configured with `pluginConfig.OnchainSubscriptions.UpdateFrequencySec = 30` (default in `core/scripts/functions/templates/oracle.toml`).
2. Attacker creates/funds a Functions subscription ≥ `MinimumSubscriptionBalance`.
3. Wait for one sync tick so `GetMaxUserBalance(attacker)` reflects a sufficient balance in the gateway node's in-memory cache.
4. Attacker withdraws the subscription's on-chain balance to zero via the `FunctionsRouter` contract (`cancelSubscription`/`withdraw`-style call, standard subscription owner action).
5. Within the next `~UpdateFrequencySec` window (or longer, per the batched `queryLoop` range logic when many subscriptions exist), attacker sends `MethodSecretsSet` gateway requests; `connector_handler.go`'s check `h.subscriptions.GetMaxUserBalance(fromAddr)` still returns the stale, pre-withdrawal balance, so the request is accepted and secrets are stored in S4, despite the subscription being actually insolvent.

### Citations

**File:** core/services/functions/connector_handler.go (L143-156)
```go
	switch body.Method {
	case functions.MethodSecretsList:
		h.handleSecretsList(ctx, gatewayID, body, fromAddr)
	case functions.MethodSecretsSet:
		if balance, err := h.subscriptions.GetMaxUserBalance(fromAddr); err != nil || balance.Cmp(h.minimumBalance.ToInt()) < 0 {
			h.lggr.Errorw("user subscription has insufficient balance", "id", gatewayID, "address", fromAddr, "balance", balance, "minBalance", h.minimumBalance)
			response := functions.ResponseBase{
				Success:      false,
				ErrorMessage: "user subscription has insufficient balance",
			}
			h.sendResponseAndLog(ctx, gatewayID, body, response)
			return nil
		}
		h.handleSecretsSet(ctx, gatewayID, body, fromAddr)
```

**File:** core/services/gateway/handlers/functions/subscriptions/subscriptions.go (L125-129)
```go
func (s *onchainSubscriptions) GetMaxUserBalance(user common.Address) (*big.Int, error) {
	s.rwMutex.RLock()
	defer s.rwMutex.RUnlock()
	return s.subscriptions.GetMaxUserBalance(user)
}
```

**File:** core/services/gateway/handlers/functions/subscriptions/subscriptions.go (L131-178)
```go
func (s *onchainSubscriptions) queryLoop() {
	defer s.closeWait.Done()

	ticker := time.NewTicker(s.updateTimeout)
	defer ticker.Stop()

	start := uint64(1)
	lastKnownCount := uint64(0)

	queryFunc := func() {
		ctx, cancel := s.stopCh.CtxWithTimeout(s.updateTimeout)
		defer cancel()

		latestBlockHeight, err := s.client.LatestBlockHeight(ctx)
		if err != nil || latestBlockHeight == nil {
			s.lggr.Errorw("Error calling LatestBlockHeight", "err", err, "latestBlockHeight", latestBlockHeight)
			return
		}

		blockNumber := big.NewInt(0).Sub(latestBlockHeight, s.blockConfirmations)

		if lastKnownCount == 0 || start > lastKnownCount {
			count, err := s.getSubscriptionsCount(ctx, blockNumber)
			if err != nil {
				s.lggr.Errorw("Error getting new subscriptions count", "err", err)
			} else {
				s.lggr.Infow("Updated subscriptions count", "count", count, "blockNumber", blockNumber.Int64())
				lastKnownCount = count
			}
		}

		if lastKnownCount == 0 {
			s.lggr.Info("Router has no subscriptions yet")
			return
		}

		if start > lastKnownCount {
			start = 1
		}

		end := min(start+uint64(s.config.UpdateRangeSize)-1, lastKnownCount)
		if err := s.querySubscriptionsRange(ctx, blockNumber, start, end); err != nil {
			s.lggr.Errorw("Error querying subscriptions", "err", err, "start", start, "end", end)
			return
		}

		start = end + 1
	}
```

**File:** core/services/gateway/handlers/functions/subscriptions/user_subscriptions.go (L70-83)
```go
func (us *userSubscriptions) GetMaxUserBalance(user common.Address) (*big.Int, error) {
	subs, exists := us.userSubscriptionsMap[user]
	if !exists {
		return nil, ErrUserHasNoSubscription
	}

	maxBalance := big.NewInt(0)
	for _, sub := range subs {
		if sub.Balance.Cmp(maxBalance) > 0 {
			maxBalance = sub.Balance
		}
	}
	return maxBalance, nil
}
```
