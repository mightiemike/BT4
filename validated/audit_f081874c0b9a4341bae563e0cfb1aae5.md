### Title
Context Hub `MemoryBank` API lacks origin/user authorization controls - (File: `chrome/browser/context_hub/memory_bank/in_memory_memory_bank.cc`)

### Summary
The Context Hub `MemoryBank` stores page-context entries but performs no ownership, origin, or profile checks when enumerating, reading, updating, or deleting entries by ID, matching the authorization-bypass bug class in the Open WebUI memories API. [1](#0-0) 

### Finding Description
`MemoryBank` exposes `GetAllEntries`, `GetEntriesByIds`, `DeleteEntries`, and `UpdateEntryAnnotations` that operate purely on `int64_t` entry IDs. [1](#0-0)  `InMemoryMemoryBank` returns, updates, or erases entries solely by ID without any origin, user, or profile check. [2](#0-1)  `MemoryBankEntry` contains only `type`, `url`, `tab_title`, `selected_text`, `tags`, `note`, `collection`, `timestamp`, and `id`, with no `origin`, `profile`, or `user_id` field. [3](#0-2)  `DatabaseMemoryBank` forwards these calls unchanged to `ContextHubBackend`, so the authorization gap persists in the persistent backend path. [4](#0-3) 

### Impact Explanation
A caller that reaches the Context Hub memory-bank interface can read arbitrary stored memories, including page URLs, titles, and selected text, or overwrite and delete entries belonging to other origins or profiles. [5](#0-4)  This is a cross-origin disclosure and unauthorized data-modification vulnerability in the same class as the reported Open WebUI issue. 

### Likelihood Explanation
The likelihood is high if `ContextHubService` exposes `MemoryBank` to renderer processes via Mojo, which is the standard pattern for `chrome/browser` services.  Because the data layer performs no authorization, the only protection would have to come from the Mojo binder or service facade; the absence of ownership fields in `MemoryBankEntry` indicates such checks were not designed into the storage layer. [3](#0-2)  I could not locate the Mojo/service binding file in the available index to confirm the exact caller surface, but the `MemoryBank` API surface itself is unguarded. 

### Recommendation
Add an `origin`/`profile`/`user` ownership field to `MemoryBankEntry`, scope all backend queries to the caller's identity, and validate ownership in `GetAllEntries`, `GetEntriesByIds`, `DeleteEntries`, `UpdateEntryAnnotations`, and `SaveMemoryBankEntry` before returning or mutating data. 

### Proof of Concept
A compromised renderer or page with access to the Context Hub Mojo interface calls `MemoryBank::GetAllEntries()` and receives every stored memory, or calls `DeleteEntries({victim_entry_id})` / `UpdateEntryAnnotations(victim_entry_id, ...)` to modify another origin's memory. [2](#0-1)  The `InMemoryMemoryBank` methods execute these operations without any ownership check. [5](#0-4)

### Citations

**File:** chrome/browser/context_hub/memory_bank/memory_bank.h (L22-47)
```text
  using OperationCompleteCallback = base::OnceCallback<void(bool)>;
  // Saves or updates an entry in the memory bank.
  virtual void SaveMemoryBankEntry(MemoryBankEntry entry,
                                   OperationCompleteCallback callback) = 0;
  // Updates the annotations (tags, note, collection) for an existing entry.
  virtual void UpdateEntryAnnotations(int64_t id,
                                      std::vector<std::string> tags,
                                      std::optional<std::string> note,
                                      std::optional<std::string> collection,
                                      OperationCompleteCallback callback) = 0;
  // Deletes entries from the memory bank.
  virtual void DeleteEntries(base::span<const int64_t> ids,
                             OperationCompleteCallback callback) = 0;
  using GetEntriesCallback =
      base::OnceCallback<void(std::vector<MemoryBankEntry>)>;
  // Returns all entries from the memory bank via the callback.
  virtual void GetAllEntries(GetEntriesCallback callback) const = 0;
  // Returns entries for the given IDs from the memory bank via the callback.
  virtual void GetEntriesByIds(base::span<const int64_t> ids,
                               GetEntriesCallback callback) const = 0;
  using GetStringsCallback =
      base::OnceCallback<void(const std::vector<std::string>&)>;
  // Returns all unique tags from the memory bank via the callback.
  virtual void GetAllTags(GetStringsCallback callback) const = 0;
  // Returns all unique collections from the memory bank via the callback.
  virtual void GetAllCollections(GetStringsCallback callback) const = 0;
```

**File:** chrome/browser/context_hub/memory_bank/in_memory_memory_bank.cc (L43-99)
```text
void InMemoryMemoryBank::UpdateEntryAnnotations(
    int64_t id,
    std::vector<std::string> tags,
    std::optional<std::string> note,
    std::optional<std::string> collection,
    OperationCompleteCallback callback) {
  auto it = entries_.Peek(id);
  if (it == entries_.end()) {
    if (callback) {
      std::move(callback).Run(/*success=*/false);
    }
    return;
  }
  it->second.tags = std::move(tags);
  it->second.note = std::move(note);
  it->second.collection = std::move(collection);
  if (callback) {
    std::move(callback).Run(/*success=*/true);
  }
}

void InMemoryMemoryBank::GetAllEntries(GetEntriesCallback callback) const {
  std::vector<MemoryBankEntry> result;
  for (const auto& [id, entry] : entries_) {
    result.push_back(entry);
  }
  if (callback) {
    std::move(callback).Run(std::move(result));
  }
}

void InMemoryMemoryBank::GetEntriesByIds(base::span<const int64_t> ids,
                                         GetEntriesCallback callback) const {
  std::vector<MemoryBankEntry> result;
  for (int64_t id : ids) {
    auto it = entries_.Peek(id);
    if (it != entries_.end()) {
      result.push_back(it->second);
    }
  }
  if (callback) {
    std::move(callback).Run(std::move(result));
  }
}

void InMemoryMemoryBank::DeleteEntries(base::span<const int64_t> ids,
                                       OperationCompleteCallback callback) {
  for (int64_t id : ids) {
    auto it = entries_.Peek(id);
    if (it != entries_.end()) {
      entries_.Erase(it);
    }
  }
  if (callback) {
    std::move(callback).Run(/*success=*/true);
  }
}
```

**File:** chrome/browser/context_hub/memory_bank/memory_bank_entry.cc (L17-24)
```text
MemoryBankEntry::MemoryBankEntry(MemoryBankType type,
                                 GURL url,
                                 std::string tab_title,
                                 std::optional<std::string> selected_text)
    : type(type),
      url(std::move(url)),
      tab_title(std::move(tab_title)),
      selected_text(std::move(selected_text)) {}
```

**File:** chrome/browser/context_hub/memory_bank/database_memory_bank.cc (L23-64)
```text
void DatabaseMemoryBank::SaveMemoryBankEntry(
    MemoryBankEntry entry,
    OperationCompleteCallback callback) {
  if (entry.timestamp.is_null()) {
    entry.timestamp = base::Time::Now();
  }
  context_hub_backend_->AddOrUpdateMemoryBankEntry(std::move(entry),
                                                   std::move(callback));
}

void DatabaseMemoryBank::UpdateEntryAnnotations(
    int64_t id,
    std::vector<std::string> tags,
    std::optional<std::string> note,
    std::optional<std::string> collection,
    OperationCompleteCallback callback) {
  context_hub_backend_->UpdateMemoryBankEntryAnnotations(
      id, std::move(tags), std::move(note), std::move(collection),
      std::move(callback));
}

void DatabaseMemoryBank::DeleteEntries(base::span<const int64_t> ids,
                                       OperationCompleteCallback callback) {
  context_hub_backend_->DeleteMemoryBankEntries(ids, std::move(callback));
}

void DatabaseMemoryBank::GetAllEntries(GetEntriesCallback callback) const {
  context_hub_backend_->GetAllMemoryBankEntries(std::move(callback));
}

void DatabaseMemoryBank::GetEntriesByIds(base::span<const int64_t> ids,
                                         GetEntriesCallback callback) const {
  context_hub_backend_->GetMemoryBankEntriesByIds(ids, std::move(callback));
}

void DatabaseMemoryBank::GetAllTags(GetStringsCallback callback) const {
  context_hub_backend_->GetAllMemoryBankTags(std::move(callback));
}

void DatabaseMemoryBank::GetAllCollections(GetStringsCallback callback) const {
  context_hub_backend_->GetAllMemoryBankCollections(std::move(callback));
}
```
