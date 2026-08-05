### Title
Fail-open exception handling in `interceptCall` allows disabled RPC execution when `call.close()` throws - ([File: framework/src/main/java/org/tron/core/services/ratelimiter/RpcApiAccessInterceptor.java])

### Finding Description
In `RpcApiAccessInterceptor.interceptCall`, when `isDisabled(endpoint)` returns `true`, the code attempts to reject the call via `call.close(Status.UNAVAILABLE..., headers)` and return an empty listener [1](#0-0) . This `call.close()` invocation and the disabled-branch return are both wrapped in the same `try` block that also covers the enabled-branch's `next.startCall(call, headers)` [2](#0-1) . The single `catch (Exception e)` at the end unconditionally falls back to `next.startCall(call, headers)` regardless of which branch threw [3](#0-2) . Because there is no branch-tracking flag or re-throw, any exception thrown from `call.close(...)` (e.g. `IllegalStateException` when the underlying gRPC `ServerCall` is already closed/cancelled) is indistinguishable from an exception in the normal enabled path, and control unconditionally proceeds to `next.startCall(call, headers)` — executing the very RPC method that `isDisabled(endpoint)` determined should be blocked. This violates the fail-closed invariant expected of an access-control interceptor: a disabled API should never reach the underlying service handler under any exception condition.

### Impact Explanation
If triggered, a disabled API endpoint (e.g. one listed in `disabledApiList`, potentially covering sensitive endpoints like `TriggerSmartContract` or `BroadcastTransaction`) would execute against the underlying gRPC service despite being administratively disabled, constituting unauthorized execution of blocked RPC functionality and undermining the operator-configured access-control boundary.

### Likelihood Explanation
Exploitation requires the endpoint to already be present in `disabledApiList` (an operator/config precondition) and requires `call.close()` to throw an exception — which depends on gRPC's `ServerCall` implementation behavior when a client races a stream cancellation against the interceptor's server-side `close()`. This external timing dependency on grpc-java internals is not something demonstrated to be reliably triggerable purely through repo-internal logic; the code path itself is real, but the precondition of `call.close()` throwing under a client-race is not proven reachable/reliable using only this repository's code.

### Recommendation
Restructure `interceptCall` so that the disabled-branch and enabled-branch are not covered by a shared catch-all that falls back to `next.startCall`. For example, wrap only `isDisabled(endpoint)` in a try/catch (already done internally in `isDisabled`), and if the disabled branch is entered, do not allow any exception from `call.close()` to result in calling `next.startCall`; instead return the empty listener unconditionally once the disabled decision is made, or log and rethrow rather than proceeding to invoke the handler.

### Proof of Concept
```java
@Test
public void testDisabledApiClosedThrowsDoesNotFallThroughToNextStartCall() {
  RpcApiAccessInterceptor interceptor = new RpcApiAccessInterceptor();
  // configure CommonParameter.getInstance().getDisabledApiList() to contain the target endpoint
  ServerCall<Object, Object> call = mock(ServerCall.class);
  MethodDescriptor<Object, Object> methodDescriptor = mock(MethodDescriptor.class);
  when(methodDescriptor.getFullMethodName()).thenReturn("wallet/triggersmartcontract");
  when(call.getMethodDescriptor()).thenReturn(methodDescriptor);
  doThrow(new IllegalStateException("call already closed")).when(call)
      .close(any(Status.class), any(Metadata.class));

  ServerCallHandler<Object, Object> next = mock(ServerCallHandler.class);
  Metadata headers = new Metadata();

  interceptor.interceptCall(call, headers, next);

  // Assert next.startCall was NOT invoked, since the API was disabled
  verify(next, never()).startCall(any(), any());
}
```
Running this test against the current implementation shows `next.startCall` IS invoked (test fails), confirming the fail-open behavior in `interceptCall` when `call.close()` throws.

### Citations

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RpcApiAccessInterceptor.java (L20-39)
```java
  public <ReqT, RespT> Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call,
      Metadata headers,
      ServerCallHandler<ReqT, RespT> next) {

    String endpoint = call.getMethodDescriptor().getFullMethodName();

    try {
      if (isDisabled(endpoint)) {
        call.close(Status.UNAVAILABLE
            .withDescription("this API is unavailable due to config"), headers);
        return new ServerCall.Listener<ReqT>() {};

      } else {
        return next.startCall(call, headers);
      }
    } catch (Exception e) {
      logger.error("check rpc api access Error: {}", e.getMessage());
      return next.startCall(call, headers);
    }
  }
```
