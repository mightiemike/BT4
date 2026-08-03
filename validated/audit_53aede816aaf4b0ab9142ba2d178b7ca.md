[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/block-executor/src/worker_pool.rs (L22-33)
```rust
struct State {
    // Sum of `num_tasks` across all in-flight `scope` calls.
    in_flight: usize,
    // Number of worker threads we have created.
    spawned: usize,
}

pub(crate) struct WorkerPool {
    sender: crossbeam::channel::Sender<Task>,
    receiver: crossbeam::channel::Receiver<Task>,
    state: Mutex<State>,
}
```

**File:** aptos-move/block-executor/src/worker_pool.rs (L115-135)
```rust
        if state.spawned < target {
            info!(
                "Growing par_exec worker pool from {} to {} thread(s)",
                state.spawned, target
            );
            while state.spawned < target {
                let receiver = self.receiver.clone();
                let id = state.spawned;
                std::thread::Builder::new()
                    .name(format!("par_exec-{}", id))
                    .spawn(move || {
                        while let Ok(task) = receiver.recv() {
                            task();
                        }
                        info!("par_exec worker {} exiting (channel disconnected)", id);
                    })
                    .expect("Failed to spawn par_exec worker thread");
                state.spawned += 1;
            }
            PAR_EXEC_POOL_SIZE.set(state.spawned as i64);
        }
```
