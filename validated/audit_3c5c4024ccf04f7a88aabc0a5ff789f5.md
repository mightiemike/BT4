Based on my investigation, I found a concrete analog to the reported bug class within the chainlink-017 codebase.

### Title
ShardOrchestratorService gRPC server accepts unauthenticated `ReportWorkflowTriggerRegistration` and `GetWorkflowShardMapping` requests, allowing workflow-to-shard routing tampering - (File: `core/services/shardorchestrator/shard_orchestrator.go`, `core/services/shardorchestrator/service.go`)

### Summary
The Shard Orchestrator gRPC service, which is responsible for maintaining the authoritative workflow-to-shard routing table used to direct workflow execution across the sharded node fleet, is exposed on a plain TCP listener using an unauthenticated `grpc.NewServer()` instance with no TLS, no interceptors, and no signature/authentication field in the request messages themselves.

### Finding Description
`ShardOrchestrator.New` creates a bare gRPC server with no credentials or authentication interceptor and registers the `Server` (implementing `ringpb.ShardOrchestratorServiceServer`) directly onto it: [1](#0-0) . The server is then bound to a TCP listener on a configurable port (`ShardOrchestratorPort`, default `50051`) with no additional network-layer restriction visible in this code path: [2](#0-1) .

The RPC handlers themselves perform no caller identity or signature verification before mutating shared routing state. `ReportWorkflowTriggerRegistration` takes `req.SourceShardId` and a list of workflow IDs directly from the wire and calls `s.ringStore.RegisterWorkflowsFromShard(req.SourceShardId, workflowIDs)` unconditionally: [3](#0-2) . Likewise, `GetWorkflowShardMapping` accepts an arbitrary list of `WorkflowIds` and, for any IDs not already mapped, calls `s.ringStore.SubmitWorkflowsForAllocation(missing)`, feeding attacker-controlled workflow identifiers into the allocation pipeline: [4](#0-3) .

The underlying `ring.Store.RegisterWorkflowsFromShard` method blindly writes `shardID` into `routingState[wfID]` for any workflow ID not already present, with no validation that the caller-provided `shardID` is a legitimate/healthy shard or that the caller is a legitimate shard node: [5](#0-4) . This is structurally the same bug class as the reported EigenDA issue: a network-reachable RPC message (`ReportWorkflowTriggerRegistrationRequest`/`GetWorkflowShardMappingRequest`) carries no signature or authentication root, so the server cannot attribute the request to a legitimate shard, and will service the request from any client able to reach the TCP port.

The Arbiter service exhibits the identical pattern: `grpc.NewServer()` with no credentials, gRPC reflection enabled (`reflection.Register(grpcServer)`, making the API easily enumerable via `grpcurl`), and a `GetDesiredReplicas` handler that stores caller-supplied shard health status directly into scaling state without verifying the sender: [6](#0-5) [7](#0-6) .

### Impact Explanation
An unauthenticated network client that can reach the `ShardOrchestratorPort` (or `ArbiterPort`) can:
- Inject forged workflow-to-shard mappings via `ReportWorkflowTriggerRegistration`, causing workflow executions to be misrouted to an attacker-chosen (or nonexistent) shard ID, which is a form of routing/data tampering affecting workflow execution correctness across the DON.
- Flood `GetWorkflowShardMapping` with bogus workflow IDs to push spurious entries into the bounded `allocRequests` channel (`AllocationRequestChannelCapacity = 1000`), potentially starving legitimate allocation requests and causing them to be dropped (denial of service against the routing/allocation subsystem): [8](#0-7) .
- Poison the Arbiter's `state.SetCurrentReplicas` with forged shard-health data, influencing scaling decisions consumed by Ring OCR.

This maps directly to "misreporting/data tampering" and "unauthorized privileged node action" impact categories, since routing state is treated as authoritative input for directing workflow execution.

### Likelihood Explanation
Likelihood depends on network exposure of the `ShardOrchestratorPort`/`ArbiterPort`. Within a Kubernetes/cluster deployment where these ports are only reachable from other node pods, exploitation requires prior network access to the internal service mesh — but nothing in the reviewed code enforces this at the application layer; the protection is purely operational/network-layer, matching exactly the same trust-boundary gap flagged in the original report (no signature field, no way to attribute the request to an authorized peer). Any misconfiguration, or an attacker with any network-position advantage (e.g., another compromised pod, ingress misconfiguration, or an operator who exposes the port for debugging as `docs/CONFIG.md` implies is configurable) can immediately exploit this without needing credentials.

### Recommendation
Add authentication to the `ShardOrchestratorService` and `Arbiter` gRPC APIs:
- Require mTLS (`grpc.Creds`) between shard orchestrator/arbiter servers and their expected shard-node clients instead of `insecure.NewCredentials()`.
- Add a signed request field (e.g., a per-shard signature over `SourceShardId` + workflow ID list) to `ReportWorkflowTriggerRegistrationRequest` and verify it server-side before calling `RegisterWorkflowsFromShard`, mirroring the recommendation to add a disperser signature field for `StoreChunksRequest`.
- Remove `reflection.Register(grpcServer)` from the Arbiter's production gRPC server or gate it behind an explicit debug flag, since it aids unauthenticated API enumeration.

### Proof of Concept
1. Deploy a node with `ShardingEnabled = true` and note the configured `ShardOrchestratorPort` (default `50051`).
2. From any host with network access to that port, use `grpcurl` (aided by reflection if the Arbiter is targeted, or the published `ringpb` proto for the orchestrator) to call:
   ```
   grpcurl -plaintext -d '{"source_shard_id": 99, "registered_workflows": {"victim-workflow-id": {}}, "total_active_workflows": 1}' <host>:50051 ring.ShardOrchestratorService/ReportWorkflowTriggerRegistration
   ```
3. Observe that `ring.Store.RegisterWorkflowsFromShard` in `core/services/ring/store.go` accepts the forged shard ID and workflow ID with no signature check, mapping `victim-workflow-id` to shard `99` in the authoritative routing table used by the DON to direct execution.

### Citations

**File:** core/services/shardorchestrator/shard_orchestrator.go (L40-55)
```go
func New(port int, ringStore *ring.Store, lggr logger.Logger) ShardOrchestrator {
	lggr = logger.Named(lggr, "ShardOrchestrator")

	grpcHandler := NewServer(ringStore, lggr)

	grpcServer := grpc.NewServer()
	grpcHandler.RegisterWithGRPCServer(grpcServer)

	return &orchestrator{
		grpcServer:  grpcServer,
		grpcHandler: grpcHandler,
		lggr:        lggr,
		grpcAddr:    fmt.Sprintf(":%d", port),
		stopCh:      make(services.StopChan),
	}
}
```

**File:** core/services/shardorchestrator/shard_orchestrator.go (L73-96)
```go
func (o *orchestrator) runGRPCServer(ctx context.Context) {
	var lc net.ListenConfig
	lis, err := lc.Listen(ctx, "tcp", o.grpcAddr)
	if err != nil {
		o.lggr.Errorw("Failed to listen for gRPC", "addr", o.grpcAddr, "error", err)
		return
	}

	o.listenerMu.Lock()
	o.listener = lis
	o.listenerMu.Unlock()
	o.lggr.Infow("gRPC server listening", "addr", lis.Addr().String())

	if err := o.grpcServer.Serve(lis); err != nil {
		// Check if this is a normal shutdown
		select {
		case <-o.stopCh:
			// Normal shutdown, don't log as error
			o.lggr.Debug("gRPC server stopped")
		default:
			o.lggr.Errorw("gRPC server error", "error", err)
		}
	}
}
```

**File:** core/services/shardorchestrator/service.go (L37-59)
```go
func (s *Server) GetWorkflowShardMapping(_ context.Context, req *ringpb.GetWorkflowShardMappingRequest) (*ringpb.GetWorkflowShardMappingResponse, error) {
	s.logger.Debugw("GetWorkflowShardMapping called", "workflowCount", len(req.WorkflowIds))

	if len(req.WorkflowIds) == 0 {
		return nil, errors.New("workflow_ids is required and must not be empty")
	}

	mappings, version := s.ringStore.GetWorkflowMappingsBatch(req.WorkflowIds)

	var missing []string
	for _, wfID := range req.WorkflowIds {
		if _, exists := mappings[wfID]; !exists {
			missing = append(missing, wfID)
		}
	}

	if len(missing) > 0 {
		dropped := s.ringStore.SubmitWorkflowsForAllocation(missing)
		s.logger.Debugw("Submitted missing workflows for allocation", "count", len(missing))
		if dropped > 0 {
			s.logger.Warnw("Allocation request channel full, workflows dropped after retries", "dropped", dropped)
		}
	}
```

**File:** core/services/shardorchestrator/service.go (L86-110)
```go
// ReportWorkflowTriggerRegistration handles shard registration reports
// Shards call this to inform shard zero about which workflows they have loaded
func (s *Server) ReportWorkflowTriggerRegistration(_ context.Context, req *ringpb.ReportWorkflowTriggerRegistrationRequest) (*ringpb.ReportWorkflowTriggerRegistrationResponse, error) {
	s.logger.Debugw("ReportWorkflowTriggerRegistration called",
		"shardID", req.SourceShardId,
		"workflowCount", len(req.RegisteredWorkflows),
		"totalActive", req.TotalActiveWorkflows,
	)

	workflowIDs := make([]string, 0, len(req.RegisteredWorkflows))
	for workflowID := range req.RegisteredWorkflows {
		workflowIDs = append(workflowIDs, workflowID)
	}

	s.ringStore.RegisterWorkflowsFromShard(req.SourceShardId, workflowIDs)

	s.logger.Infow("Successfully registered workflows",
		"shardID", req.SourceShardId,
		"workflowCount", len(workflowIDs),
	)

	return &ringpb.ReportWorkflowTriggerRegistrationResponse{
		Success: true,
	}, nil
}
```

**File:** core/services/ring/store.go (L279-296)
```go
func (s *Store) RegisterWorkflowsFromShard(shardID uint32, workflowIDs []string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	for _, wfID := range workflowIDs {
		if _, exists := s.routingState[wfID]; !exists {
			s.routingState[wfID] = shardID
			s.routingStateMeta[wfID] = &MappingMeta{
				OldShardID:   0,
				NewShardID:   shardID,
				InTransition: false,
				UpdatedAt:    now,
			}
		}
	}
	s.mappingVersion++
}
```

**File:** core/services/ring/store.go (L337-361)
```go
func (s *Store) SubmitWorkflowsForAllocation(workflowIDs []string) (dropped int) {
	s.mu.Lock()
	defer s.mu.Unlock()

	for _, wfID := range workflowIDs {
		if _, exists := s.routingState[wfID]; !exists {
			enqueued := false
			for attempt := 0; attempt < submitAllocRetries && !enqueued; attempt++ {
				select {
				case s.allocRequests <- AllocationRequest{WorkflowID: wfID, Result: nil}:
					enqueued = true
				default:
					if attempt < submitAllocRetries-1 {
						s.mu.Unlock()
						time.Sleep(submitAllocRetryInterval)
						s.mu.Lock()
					} else {
						dropped++
					}
				}
			}
		}
	}
	return dropped
}
```

**File:** core/services/arbiter/arbiter.go (L71-81)
```go
	// Create gRPC server and register both services
	grpcServer := grpc.NewServer()
	ringpb.RegisterArbiterServer(grpcServer, grpcHandler)
	ringpb.RegisterArbiterScalerServer(grpcServer, ringArbiterHandler)

	// Register gRPC health check service
	healthServer := health.NewServer()
	healthgrpc.RegisterHealthServer(grpcServer, healthServer)

	// Register gRPC server reflection (enables grpcurl and other tools)
	reflection.Register(grpcServer)
```

**File:** core/services/arbiter/grpc_server.go (L35-53)
```go
func (s *GRPCServer) GetDesiredReplicas(ctx context.Context, req *ringpb.ShardStatusRequest) (*ringpb.ArbiterResponse, error) {
	// Store incoming shard status in State so Ring OCR can access it via ArbiterScaler.Status()
	if s.state != nil && len(req.GetStatus()) > 0 {
		replicas := s.convertProtoStatusToReplicas(req.GetStatus())
		s.state.SetCurrentReplicas(replicas)
		s.lggr.Debugw("Updated shard status from scaler",
			"shardCount", len(replicas),
		)
	}

	// Get desired shard count from ShardConfig contract
	shardCount, err := s.shardConfig.GetDesiredShardCount(ctx)
	if err != nil {
		s.lggr.Errorw("Failed to get desired shard count",
			"error", err,
		)
		RecordRequest("GetDesiredReplicas", "INTERNAL")
		return nil, status.Error(codes.Internal, "failed to get desired shard count")
	}
```
