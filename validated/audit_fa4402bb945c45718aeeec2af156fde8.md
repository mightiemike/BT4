### Title
Unbounded RPC permit exhaustion via never-completing streams in `GlobalPreemptibleStrategy` - ([File: framework/src/main/java/org/tron/core/services/ratelimiter/strategy/GlobalPreemptibleStrategy.java])

### Summary
`GlobalPreemptibleAdapter.acquire()`/`tryAcquire()` delegate to a plain `java.util.concurrent.Semaphore` with no forced-expiry or lease timeout, and permit release is entirely delegated to gRPC's `onComplete`/`onCancel` listener callbacks in `RateLimiterInterceptor`. An unprivileged client that opens `permit` (N) concurrent calls to a rate-limited endpoint and never half-closes/completes/cancels those calls can hold all N permits indefinitely, causing all other legitimate `acquire()` attempts to fail with `RESOURCE_EXHAUSTED` for as long as the attacker keeps the connections open.

### Finding Description
`RateLimiterInterceptor.interceptCall` acquires a per-endpoint permit via `rateLimiter.acquirePermit(runtimeData)` *before* `next.startCall()` is invoked [1](#0-0) . The permit is only released in the `onComplete`/`onCancel` overrides of the forwarded `ServerCall.Listener`, or in the exception path if `next.startCall()` throws [2](#0-1) . These callbacks are driven purely by gRPC transport-level call termination (client half-close/cancel, or connection loss) — there is no application-level lease timer that force-releases a permit after it's been held too long.

`GlobalPreemptibleAdapter.tryAcquire(RuntimeData)`/`acquire(RuntimeData)` simply delegate to `GlobalPreemptibleStrategy.tryAcquire()`/`acquire()` [3](#0-2) . `GlobalPreemptibleStrategy` wraps a bare `Semaphore` sized by the `permit` config param, and neither `tryAcquire()` nor `acquire()` attach any per-acquisition expiry; `acquire()` only bounds how long a *waiting caller* blocks (`DEFAULT_ACQUIRE_TIMEOUT` = 2s), not how long a *holder* may keep the permit [4](#0-3) . `release()` is a bare `sp.release()` with no automatic trigger [5](#0-4) .

Consequently, if `apiNonBlocking=false` forces the blocking `acquire()` path for a critical endpoint configured with a small `permit` count (e.g. `permit=1`, per the `reference.conf`/`docs/configuration.md` example for `GlobalPreemptibleAdapter`), an attacker can open `permit` long-lived RPC calls that never trigger `onHalfClose`/`onComplete`/`onCancel` (e.g. never sending the final frame on a stream, or simply not tearing down the call), holding the semaphore permits forever. Every subsequent legitimate caller's `acquire()` call blocks up to 2 seconds and then is rejected with `RESOURCE_EXHAUSTED`, repeatedly, for as long as the attacker's connections stay open.

The only mitigating control is at the transport layer, not in the rate limiter: `RpcService.initServerBuilder()` sets `maxConnectionIdle` and `maxConnectionAge` on the `NettyServerBuilder` [6](#0-5) . `maxConnectionAge` will eventually force-close a connection regardless of activity, which should trigger `onCancel` and release the permit — but this is a connection-level bound configured independently of the rate limiter, is not documented as a safeguard for this specific attack, and its default value/effectiveness against a client that reconnects immediately after being closed was not verified in this investigation (i.e., an attacker can simply reopen new never-closing streams as fast as old ones are force-closed, since there is no additional cost or per-account/per-IP restriction on opening a new call). There is no evidence of any actuator-level, signature, or fee-based check that would prevent an unprivileged client from opening such calls, since permit acquisition happens at the gRPC interceptor level, before any transaction/actuator validation.

### Impact Explanation
This is a DoS via RPC-API affecting availability of critical asset-moving endpoints (e.g. `Wallet/BroadcastTransaction` if configured with `GlobalPreemptibleAdapter`). Because the permit pool is global (bounded by a small `permit` count in the reference config) and release is contingent solely on gRPC listener callbacks with no forced expiry inside `GlobalPreemptibleStrategy`/`GlobalPreemptibleAdapter`, a small number of never-completing connections (as few as `permit`, e.g. 1) can deny all other unprivileged users the ability to broadcast transactions through that node's RPC interface, for as long as those connections are kept alive (bounded only by the independently-configured `maxConnectionAge`/`maxConnectionIdle`, if set to a finite value).

### Likelihood Explanation
Preconditions: the endpoint must be configured to use `GlobalPreemptibleAdapter` with a small `permit` value and `apiNonBlocking=false` (both plausible per the shipped `reference.conf` example and `docs/configuration.md`). The attack requires no privileges, no fees, no signed transaction — only opening `permit`-many gRPC connections/calls to the targeted method and withholding call completion. This is cheap, repeatable, and does not require bypassing signature/actuator/fork checks since the block occurs entirely at the interceptor layer before any transaction processing. Full unbounded persistence depends on whether `maxConnectionAge`/`maxConnectionIdle` are set to a finite, low value in the deployed configuration, and whether the attacker can trivially reopen streams after any forced connection closure — this last point (attacker's ability to sustain the attack against churn) could not be fully confirmed from static config alone but appears likely given each new attempt is essentially free.

### Recommendation
Add a forced-expiry/lease mechanism to `GlobalPreemptibleStrategy` (e.g., a `Semaphore` replaced or wrapped with per-acquisition timestamps and a background reaper that force-releases permits held longer than a configurable max lease duration), or attach an explicit gRPC `Context.withDeadline`/server-side call deadline to permit-guarded calls in `RateLimiterInterceptor` so that permits are always released after a bounded time regardless of client behavior. Additionally, ensure `maxConnectionAge`/`maxConnectionIdle` have sane, low, non-infinite defaults for permit-guarded endpoints, and consider per-IP/per-account caps on concurrent open calls to permit-guarded methods to prevent trivial reconnection-based sustaining of the attack.

### Proof of Concept
```java
// Illustrative JUnit-style PoC against GlobalPreemptibleStrategy directly
// (mirrors AdaptorTest.java patterns in the repo)
GlobalPreemptibleAdapter limiter = new GlobalPreemptibleAdapter("permit=1");

// Attacker "holds" the only permit by never calling release()
assertTrue(limiter.acquire(mockRuntimeData())); // simulates a call that never completes

// Legitimate user's subsequent BroadcastTransaction-equivalent call
long start = System.currentTimeMillis();
boolean legitimateAcquired = limiter.acquire(mockRuntimeData());
long elapsed = System.currentTimeMillis() - start;

assertFalse(legitimateAcquired);           // request is rejected
assertTrue(elapsed >= 2000);               // blocked for the full DEFAULT_ACQUIRE_TIMEOUT
// Repeating this call indefinitely, without the attacker ever calling release(),
// demonstrates permanent denial of service for the guarded endpoint.
```
At the RPC level: open `permit` gRPC calls to the configured method (e.g. via a raw HTTP/2 client) and never send `END_STREAM`/never cancel; then issue an (N+1)th call to the same method and observe it always receives `Status.RESOURCE_EXHAUSTED` from `RateLimiterInterceptor.interceptCall` [7](#0-6)  for as long as the attacker's connections remain open.

### Citations

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L109-114)
```java
    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L116-123)
```java
    if (!acquireResource) {
      // Release the per-endpoint permit when global rejected, to avoid semaphore leak.
      if (rateLimiter instanceof IPreemptibleRateLimiter && perEndpointAcquired) {
        ((IPreemptibleRateLimiter) rateLimiter).release();
      }
      call.close(Status.fromCode(Code.RESOURCE_EXHAUSTED), new Metadata());
      return listener;
    }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L129-150)
```java
      listener = new SimpleForwardingServerCallListener<ReqT>(delegate) {
        @Override
        public void onComplete() {
          // must release the permit to avoid the leak of permit.
          if (rateLimiter instanceof IPreemptibleRateLimiter) {
            ((IPreemptibleRateLimiter) rateLimiter).release();
          }
        }

        @Override
        public void onCancel() {
          // must release the permit to avoid the leak of permit.
          if (rateLimiter instanceof IPreemptibleRateLimiter) {
            ((IPreemptibleRateLimiter) rateLimiter).release();
          }
        }
      };
    } catch (Exception e) {
      // next.startCall() failed — release the permit that was already acquired.
      if (rateLimiter instanceof IPreemptibleRateLimiter) {
        ((IPreemptibleRateLimiter) rateLimiter).release();
      }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/adapter/GlobalPreemptibleAdapter.java (L19-27)
```java
  @Override
  public boolean tryAcquire(RuntimeData data) {
    return strategy.tryAcquire();
  }

  @Override
  public boolean acquire(RuntimeData data) {
    return strategy.acquire();
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/GlobalPreemptibleStrategy.java (L33-47)
```java
  public boolean tryAcquire() {
    return sp.tryAcquire();
  }

  public boolean acquire() {
    try {
      return sp.tryAcquire(DEFAULT_ACQUIRE_TIMEOUT, TimeUnit.SECONDS);
    } catch (InterruptedException e) {
      // Restore the interrupt flag and reject — caller must not release a permit
      // it never acquired.
      logger.error("acquire permit with error: {}", e.getMessage());
      Thread.currentThread().interrupt();
      return false;
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/GlobalPreemptibleStrategy.java (L49-51)
```java
  public void release() {
    sp.release();
  }
```

**File:** framework/src/main/java/org/tron/common/application/RpcService.java (L103-109)
```java
    serverBuilder
        .maxConcurrentCallsPerConnection(parameter.getMaxConcurrentCallsPerConnection())
        .flowControlWindow(parameter.getFlowControlWindow())
        .maxConnectionIdle(parameter.getMaxConnectionIdleInMillis(), TimeUnit.MILLISECONDS)
        .maxConnectionAge(parameter.getMaxConnectionAgeInMillis(), TimeUnit.MILLISECONDS)
        .maxInboundMessageSize(parameter.getMaxMessageSize())
        .maxHeaderListSize(parameter.getMaxHeaderListSize());
```
