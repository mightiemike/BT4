### Title
Arbitrary local file disclosure via symlink following in File System Access directory iteration - (File: content/browser/file_system_access/file_system_access_directory_handle_impl.cc)

### Summary
The Jenkins advisory describes a workspace/artifact file browser that followed symbolic links outside the browsed directory, letting an attacker who controls file contents inside a permitted directory read arbitrary files elsewhere on disk. Chromium's `FileSystemAccessDirectoryHandleImpl`, which backs the renderer-exposed File System Access API (`FileSystemDirectoryHandle.getFileHandle()`/`.values()`/`.entries()`), has the analogous weakness: it does not verify that a symlink child of a granted directory resolves to a path *inside* the granted subtree. Instead, the only mitigation is an optional "sensitive entry" blocklist check that is itself gated behind a `base::Feature`.

### Finding Description
When a site has been granted read access to a directory via the File System Access API, `GetFileResolved()` and `DidReadDirectory()` enumerate directory entries and hand back `FileSystemAccessEntryPtr`/file handles for each child, including symlinks, based purely on `child_url` produced by `GetChildURL()`. The only extra safety check performed on symlinked children is `ConfirmSensitiveEntryAccess()`, and it is run **only if** `features::kFileSystemAccessDirectoryIterationBlocklistCheck` is enabled: [1](#0-0) [2](#0-1) 

The in-code comments explicitly describe the same threat model as the Jenkins CVE: "a child symlink file may have been created since then, pointing to a blocklisted file or directory ... Check for sensitive entry access, which is run on the resolved path." However, `ConfirmSensitiveEntryAccess` only rejects entries matching a fixed OS-sensitive-path blocklist (e.g. system directories, browser profile data) — it does **not** enforce that the resolved (realpath) target stays within the originally granted directory tree. This mirrors the incomplete Jenkins fix noted in the report ("incomplete fix for SECURITY-904"): a partial check against a denylist rather than a full containment check.

As a result, if a symlink exists inside (or is later created inside) a directory the user granted to a site — pointing to any file not in the blocklist (e.g., another project's files, another user-writable directory, SSH keys not covered by the blocklist, etc.) — script can obtain a `FileSystemFileHandle` for it and read its contents via `getFile()`/`AsBlob()`, entirely outside the boundary the user believed they were granting access to.

### Impact Explanation
This allows a malicious/compromised web page with a previously granted File System Access directory permission (a routine, low-friction grant via `showDirectoryPicker()`) to read arbitrary files on the user's local disk reachable through a symlink placed in that directory, without any further user consent, since the API assumes access is limited to the picked subtree. This is a confidentiality violation (cross-boundary local file disclosure), analogous to CWE-59 in the Jenkins advisory.

### Likelihood Explanation
Requires: (1) the site already holds a File System Access read grant for some directory (achievable via a single user gesture prompt that many users approve believing it scopes access to that folder/subtree only), and (2) a symlink existing under that directory pointing outside it. The renderer/script side can trigger directory enumeration or `getFileHandle()` purely through the standard Web-exposed FileSystemAccess API without additional native code. The bypass strength depends on whether `kFileSystemAccessDirectoryIterationBlocklistCheck` is enabled and on the blocklist's coverage, but the design itself (denylist rather than containment check) means non-blocklisted symlink targets are always exposed by design, not just as a rollout bug.

### Recommendation
Enforce a directory-containment check (verify the resolved/realpath of every child entry — especially symlinks — is a descendant of the originally granted root) unconditionally in `GetFileResolved`, `GetDirectoryResolved`, and `DidReadDirectory`, rather than relying solely on a sensitive-path blocklist gated by a feature flag. Reject or filter out entries whose resolved path escapes the granted subtree, matching the approach Jenkins ultimately took (no longer exposing symlinked content across the workspace boundary).

### Proof of Concept
1. User grants a website read access to directory `D` via `showDirectoryPicker()`.
2. `D` contains (or the site can arrange for `D` to later contain, e.g. via write access from a prior grant, or if `D` is synced/shared storage) a symbolic link `D/link` pointing to a sensitive file outside `D` that is not present in the sensitive-entry blocklist (e.g., `~/other_project/secret.txt`).
3. Script calls `directoryHandle.getFileHandle('link')` then reads its contents via `file.text()`.
4. Because `ConfirmSensitiveEntryAccess` only checks against a denylist (and is feature-gated) rather than verifying the resolved path remains within `D`, the read succeeds and discloses the out-of-scope file content to the page's origin.

### Citations

**File:** content/browser/file_system_access/file_system_access_directory_handle_impl.cc (L211-230)
```text
  if (base::FeatureList::IsEnabled(
          features::kFileSystemAccessDirectoryIterationBlocklistCheck) &&
      manager()->permission_context()) {
    // While this directory handle already has obtained the permission and
    // checked for the blocklist, a child symlink file may have been created
    // since then, pointing to a blocklisted file or directory.  Check for
    // sensitive entry access, which is run on the resolved path.
    PathInfo path_info{
        child_url.type() == storage::FileSystemType::kFileSystemTypeLocal
            ? PathType::kLocal
            : PathType::kExternal,
        child_url.path(), basename};
    manager()->permission_context()->ConfirmSensitiveEntryAccess(
        context().storage_key.origin(), path_info, HandleType::kFile,
        UserAction::kNone, context().frame_id,
        base::BindOnce(&FileSystemAccessDirectoryHandleImpl::DoGetFile,
                       weak_factory_.GetWeakPtr(), basename, create, child_url,
                       std::move(callback)));
    return;
  }
```

**File:** content/browser/file_system_access/file_system_access_directory_handle_impl.cc (L788-848)
```text
  if (base::FeatureList::IsEnabled(
          features::kFileSystemAccessDirectoryIterationBlocklistCheck) &&
      manager()->permission_context()) {
    // While this directory handle already has obtained the permission and
    // checked for the blocklist, a child symlink file may have been created
    // since then, pointing to a blocklisted file or directory. Before merging
    // a child into a result vector, check for sensitive entry access, which is
    // run on the resolved path.
    auto final_callback = base::BindOnce(
        &FileSystemAccessDirectoryHandleImpl::CurrentBatchEntriesReady,
        weak_factory_.GetWeakPtr(), std::move(listener_holder));

    // Barrier callback is used to wait for checking each path in the
    // `file_list` and creating a `FileSystemAccessEntryPtr` if the path is
    // valid; otherwise, nullptr is returned for the callback. Since the barrier
    // callback expects a fixed number of callbacks to be invoked before the
    // final callback is invoked, each item in `file_list` must trigger the
    // barrier callback with a valid `FileSystemAccessEntryPtr` or nullptr.
    auto barrier_callback = base::BarrierCallback<FileSystemAccessEntryPtr>(
        file_list.size(),
        base::BindOnce(
            &FileSystemAccessDirectoryHandleImpl::MergeCurrentBatchEntries,
            weak_factory_.GetWeakPtr(), std::move(final_callback)));

    for (const auto& entry : file_list) {
      std::string basename = storage::FilePathToString(entry.name.path());
      storage::FileSystemURL child_url;
      blink::mojom::FileSystemAccessErrorPtr get_child_url_result =
          GetChildURL(basename, &child_url);

      // Skip any entries with names that aren't allowed to be accessed by
      // this API, such as files with disallowed characters in their names.
      if (get_child_url_result->status != FileSystemAccessStatus::kOk) {
        barrier_callback.Run(nullptr);
        continue;
      }

      if (entry.type == filesystem::mojom::FsFileType::DIRECTORY) {
        auto directory_result_entry = CreateEntry(
            entry.name, entry.display_name, child_url, HandleType::kDirectory);
        barrier_callback.Run(std::move(directory_result_entry));
        continue;
      }

      // Only run sensitive entry check on a file, which could be a symbolic
      // link.
      manager()->permission_context()->ConfirmSensitiveEntryAccess(
          context().storage_key.origin(),
          PathInfo(
              child_url.type() == storage::FileSystemType::kFileSystemTypeLocal
                  ? PathType::kLocal
                  : PathType::kExternal,
              child_url.path(), basename),
          HandleType::kFile, UserAction::kNone, context().frame_id,
          base::BindOnce(&FileSystemAccessDirectoryHandleImpl::
                             DidVerifySensitiveAccessForFileEntry,
                         weak_factory_.GetWeakPtr(), entry.name,
                         entry.display_name, child_url, barrier_callback));
    }
    return;
  }
```
