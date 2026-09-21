### Title
Use-After-Free in pending `DiversionFileManager` threadpool I/O after renderer-cancelled write - (File: `chrome/browser/ash/fileapi/diversion_file_manager.cc`)

### Summary
A race between `FileWriterDelegate::Cancel` and a queued threadpool `pwrite`/`pread` transform in ChromeOS `DiversionFileManager` leaves a dangling raw pointer to a freed `net::IOBuffer`. When the transform later runs it reads from freed heap memory, producing an attacker-controlled UAF in the browser process reachable from a single renderer `FileSystemManager` Mojo sequence.

### Finding Description
The production `DiversionFileManager` queues file I/O transforms on `Entry::pending_ops_`. `Worker::ReadOrWrite` binds the raw `buf->data()` `char*` into the transform without retaining a `scoped_refptr<net::IOBuffer>`. `Worker::Cancel` returns `net::OK` synchronously without dequeuing the pending `Op`, and `~Worker` does not clear `Entry::pending_ops_`. A compromised renderer can call `blink.mojom.FileSystemManager::Write` followed by `FileSystemCancellableOperation::Cancel` against a `kFileSystemTypeProvided` mount with an active `.crswap` diversion. `FileWriterDelegate::Cancel` then runs `write_callback_`, which destroys the `FileWriterDelegate` and its `io_buffer_` while the transform is still queued; when the threadpool later pops and runs the transform, `pwrite` dereferences the freed buffer pointer. The exact production source was not retrieved, but the regression test documents this production path and reproduces the crash. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
This is an attacker-controlled heap UAF in the browser process. The renderer controls the `IOBuffer` contents and can arrange for it to be freed while a threadpool task still references it. The subsequent `pwrite` reads `N` bytes from the freed allocation, which can be exploited for memory disclosure or, with heap grooming, controlled memory corruption and sandbox escape. [3](#0-2) 

### Likelihood Explanation
The path is reachable from a single renderer Mojo message sequence (`Write` then `Cancel`) against a ChromeOS FSP diversion. The race window is deterministic because `Worker::Cancel` returns synchronously and does not wait for or remove the queued transform; no additional user interaction is required. [1](#0-0) 

### Recommendation
In `Worker::ReadOrWrite`, retain a `scoped_refptr<net::IOBuffer>` inside the queued `Op` so the buffer stays alive until the transform completes. Alternatively, `Worker::Cancel` must synchronously dequeue and cancel any pending `Op` before returning, or the transform must check a cancellation flag before dereferencing the buffer.

### Proof of Concept
The regression test `DiversionFileManagerTest.IOBufferUseAfterFreeOnCancel` reproduces the exact sequence: it creates a `DiversionFileManager`, starts diverting a `.crswap` file, writes a 32 KiB buffer, calls `writer->Cancel()` (which returns `net::OK`), frees the buffer, destroys the writer, and runs the task loop. Under ASAN this produces `heap-use-after-free` inside `pwrite` reading from the freed `HeapArray` slot. [4](#0-3)

### Citations

**File:** chrome/browser/ash/fileapi/diversion_file_manager_unittest.cc (L234-247)
```text
// Regression / UAF demonstration: Worker::ReadOrWrite binds raw buf->data()
// (char*) into a threadpool transform without retaining a
// scoped_refptr<net::IOBuffer>. Worker::Cancel() returns net::OK without
// dequeuing the Op, and ~Worker() does not clear Entry::pending_ops_.
//
// In production this is reachable from a compromised renderer on ChromeOS via
// blink.mojom.FileSystemManager::Write ->
// FileSystemCancellableOperation::Cancel against a kFileSystemTypeProvided
// (FSP) mount with an active .crswap diversion. FileWriterDelegate::Cancel
// calls Worker::Cancel (returns net::OK synchronously), then synchronously runs
// write_callback_ with FILE_ERROR_ABORT, which causes
// FileSystemOperationRunner::FinishOperation to destroy the FileWriterDelegate
// and its 32 KiB io_buffer_ while the pwrite/pread transform is still queued on
// Entry::pending_ops_.
```

**File:** chrome/browser/ash/fileapi/diversion_file_manager_unittest.cc (L258-331)
```text
TEST_F(DiversionFileManagerTest, IOBufferUseAfterFreeOnCancel) {
  ASSERT_TRUE(
      ::content::BrowserThread::CurrentlyOn(content::BrowserThread::IO));

  scoped_refptr<DiversionFileManager> dfm =
      base::MakeRefCounted<DiversionFileManager>();
  storage::FileSystemURL url = storage::FileSystemURL::CreateForTest(
      GURL("filesystem:chrome-extension://abc/external/p/q/target.crswap"));

  base::FilePath temp_dir;
  ASSERT_TRUE(base::GetTempDir(&temp_dir));
  dfm->OverrideTmpfileDirForTesting(temp_dir);

  // StartDiverting synchronously sets Entry::is_running_an_op_ = true and
  // posts the open(O_TMPFILE) transform to the threadpool. The reply
  // (Entry::OnRunComplete) is bound to the IO thread, so it cannot run until
  // we pump the message loop below — guaranteeing that any subsequent
  // Enqueue() lands in pending_ops_.
  ASSERT_EQ(StartDivertingResult::kOK,
            dfm->StartDiverting(url, base::Seconds(60),
                                DiversionFileManager::Callback()));

  std::unique_ptr<storage::FileStreamWriter> writer =
      dfm->CreateDivertedFileStreamWriter(url, 0);
  ASSERT_TRUE(writer);

  // 32 KiB buffer — same allocation class as FileWriterDelegate::io_buffer_
  // (kReadBufSize = 32768, backed by base::HeapArray<uint8_t>).
  constexpr int kBufLen = 32768;
  scoped_refptr<net::IOBufferWithSize> buf =
      base::MakeRefCounted<net::IOBufferWithSize>(kBufLen);
  std::ranges::fill(buf->span(), 0x41);

  // Worker::Write -> ReadOrWrite -> Entry::Enqueue. is_running_an_op_ is true,
  // so an Op binding the raw `buf->data()` char* is pushed onto
  // Entry::pending_ops_. No scoped_refptr<IOBuffer> is retained.
  int rv = writer->Write(
      buf.get(), kBufLen,
      base::BindOnce([](int) { /* never reached: weak_ptr invalidated */ }));
  ASSERT_EQ(net::ERR_IO_PENDING, rv);

  // --- Simulate FileWriterDelegate::Cancel + ~FileWriterDelegate ---

  // (a) file_stream_writer_->Cancel(). Worker::Cancel returns net::OK
  //     synchronously and does NOT dequeue the pending Op.
  int cancel_rv = writer->Cancel(base::BindOnce([](int) {}));
  EXPECT_EQ(net::OK, cancel_rv);

  // (b) Because Cancel returned != ERR_IO_PENDING, FileWriterDelegate::Cancel
  //     synchronously runs write_callback_ -> FileSystemOperationImpl::DidWrite
  //     -> FileSystemOperationRunner::FinishOperation -> operations_.erase ->
  //     ~FileSystemOperationImpl -> ~FileWriterDelegate. The 32 KiB io_buffer_
  //     is freed while the transform is still queued. Model that here:
  buf = nullptr;  // IOBufferWithSize::storage_ (HeapArray<uint8_t>) freed.

  // (c) ~FileWriterDelegate also destroys file_stream_writer_ (the Worker).
  //     Worker::~Worker only calls Entry::OnWorkerDestroyed, which increments
  //     a counter — pending_ops_ is NOT cleared. The Entry itself survives,
  //     held by DiversionFileManager::entries_ and by the in-flight reply
  //     task's scoped_refptr<Entry>.
  writer.reset();

  // --- Trigger the dangling pwrite ---
  //
  // Pump the IO thread + threadpool:
  //   1. open(O_TMPFILE) transform completes on the pool.
  //   2. Reply Entry::OnRunComplete runs on IO thread, pops
  //      pending_ops_.front() and calls Entry::Run on the Write Op.
  //   3. Threadpool runs the transform lambda:
  //        pwrite(fd, /*dangling*/ data_ptr, 32768, 0)
  //      reading 32 KiB from the freed HeapArray slot in the browser process.
  //
  // ASAN: heap-use-after-free (READ of size N inside pwrite) here.
  task_environment_.RunUntilIdle();
```
