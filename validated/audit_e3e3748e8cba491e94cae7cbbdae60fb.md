### Title
Path Traversal via External File Scheme - ([File: chrome/browser/ash/fileapi/external_file_url_util.cc])

### Summary
The `externalfile:` URL scheme in ChromeOS is vulnerable to path traversal because it fails to sanitize or validate the virtual path extracted from the URL. An attacker can craft a URL with `..` components that, when resolved, allows access to files outside the intended mount points within the `FileSystemContext`.

### Finding Description
The `externalfile:` scheme is handled by `ash::ExternalFileURLLoaderFactory` [1](#0-0) . When a request is made, the URL is resolved via `ash::ExternalFileResolver` [2](#0-1) . The resolver uses `ash::ExternalFileURLToVirtualPath` to convert the URL path into a `base::FilePath` [3](#0-2) .

The implementation of `ExternalFileURLToVirtualPath` simply unescapes the URL path and converts it directly to a `base::FilePath` without any validation for parent directory references (`..`) [4](#0-3) . This virtual path is then passed to `storage::FileSystemContext::CreateIsolatedURLFromVirtualPath` [5](#0-4) . While the resulting `FileSystemURL` is checked for validity and type [6](#0-5) , the underlying `FileSystemContext` operations (like `GetMetadata` and `CreateFileStreamReader`) will resolve the traversal components within the virtual path space, potentially allowing an attacker to escape the intended directory scope of a specific mount point (e.g., escaping a specific `file_system_provider` instance to access other external mount points) [7](#0-6) .

### Impact Explanation
A remote website or a compromised renderer can trigger a navigation or subresource load to an `externalfile:` URL. If the process has been granted `kExternalFileScheme` permissions (which is common for certain ChromeOS components), the attacker can use path traversal to read sensitive files from other external file systems (like Google Drive, Android files, or Removable media) that the user has mounted, bypassing intended origin-based or provider-based isolation.

### Likelihood Explanation
The vulnerability is highly reachable as it only requires the attacker to provide a malformed URL to a component that processes `externalfile:` requests. ChromeOS uses this scheme extensively for integrating various file systems, and while `ChildProcessSecurityPolicy` checks exist [8](#0-7) , once a process is authorized for the scheme, it can traverse between different virtual paths.

### Recommendation
Modify `ash::ExternalFileURLToVirtualPath` in `chrome/browser/ash/fileapi/external_file_url_util.cc` to check for parent references using `base::FilePath::ReferencesParent()`. If the path contains `..` components, it should be rejected or sanitized before being used to create a `FileSystemURL`.

### Proof of Concept
1. An attacker identifies a renderer process that has been granted `externalfile:` access (e.g., via `GrantRequestScheme`).
2. The attacker triggers a fetch or navigation to:
   `externalfile:provider_id:fs_id:user_hash/../../other_provider/sensitive_file.txt`
3. `ExternalFileURLToVirtualPath` returns a `base::FilePath` containing the `..` components.
4. `CreateIsolatedURLFromVirtualPath` creates a `FileSystemURL` with the traversing path.
5. `ExternalFileResolver` successfully calls `CreateFileStreamReader` for the traversed path, leaking the contents of `sensitive_file.txt` from a different provider.

### Citations

**File:** chrome/browser/ash/fileapi/external_file_url_loader_factory.cc (L19-22)
```text
#include "content/public/browser/browser_task_traits.h"
#include "content/public/browser/browser_thread.h"
#include "content/public/browser/child_process_host.h"
#include "content/public/browser/child_process_security_policy.h"
```

**File:** chrome/browser/ash/fileapi/external_file_url_loader_factory.cc (L234-241)
```text
    resolver_->Resolve(
        request.method, request.url,
        base::BindOnce(&ExternalFileURLLoader::CompleteWithError,
                       weak_ptr_factory_.GetWeakPtr()),
        base::BindOnce(&ExternalFileURLLoader::OnRedirectURLObtained,
                       weak_ptr_factory_.GetWeakPtr()),
        base::BindOnce(&ExternalFileURLLoader::OnStreamObtained,
                       weak_ptr_factory_.GetWeakPtr()));
```

**File:** chrome/browser/ash/fileapi/external_file_url_loader_factory.cc (L350-357)
```text
  if (render_process_host_id_ != content::ChildProcessHost::kInvalidUniqueID &&
      !content::ChildProcessSecurityPolicy::GetInstance()->CanRequestURL(
          render_process_host_id_, request.url)) {
    DVLOG(1) << "Denied unauthorized request for "
             << request.url.possibly_invalid_spec();
    ReportBadMessage("Unauthorized externalfile request");
    return;
  }
```

**File:** chrome/browser/ash/fileapi/external_file_resolver.cc (L77-77)
```text
    const base::FilePath virtual_path = ExternalFileURLToVirtualPath(url);
```

**File:** chrome/browser/ash/fileapi/external_file_resolver.cc (L80-82)
```text
    isolated_file_system_ =
        file_manager::util::CreateIsolatedURLFromVirtualPath(
            *context, url::Origin(), virtual_path);
```

**File:** chrome/browser/ash/fileapi/external_file_resolver.cc (L85-93)
```text
    if (!isolated_file_system_.url.is_valid()) {
      ReplyResult(net::ERR_INVALID_URL);
      return;
    }

    if (!IsExternalFileURLType(isolated_file_system_.url.type())) {
      ReplyResult(net::ERR_FAILED);
      return;
    }
```

**File:** chrome/browser/ash/fileapi/external_file_resolver.cc (L209-242)
```text
  file_system_context_->operation_runner()->GetMetadata(
      isolated_file_system_.url,
      {storage::FileSystemOperation::GetMetadataField::kIsDirectory,
       storage::FileSystemOperation::GetMetadataField::kSize},
      base::BindOnce(&ExternalFileResolver::OnFileInfoObtained,
                     weak_ptr_factory_.GetWeakPtr()));
}

void ExternalFileResolver::OnFileInfoObtained(
    base::File::Error error,
    const base::File::Info& file_info) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::IO);
  if (error == base::File::FILE_ERROR_NOT_FOUND) {
    std::move(error_callback_).Run(net::ERR_FILE_NOT_FOUND);
    return;
  }

  if (error != base::File::FILE_OK || file_info.is_directory ||
      file_info.size < 0) {
    std::move(error_callback_).Run(net::ERR_FAILED);
    return;
  }

  // Compute content size.
  if (!byte_range_.ComputeBounds(file_info.size)) {
    std::move(error_callback_).Run(net::ERR_REQUEST_RANGE_NOT_SATISFIABLE);
    return;
  }
  const int64_t offset = byte_range_.first_byte_position();
  const int64_t remaining_bytes = byte_range_.last_byte_position() - offset + 1;

  std::unique_ptr<storage::FileStreamReader> stream_reader =
      file_system_context_->CreateFileStreamReader(
          isolated_file_system_.url, offset, remaining_bytes, base::Time());
```

**File:** chrome/browser/ash/fileapi/external_file_url_util.cc (L47-53)
```text
base::FilePath ExternalFileURLToVirtualPath(const GURL& url) {
  if (!url.is_valid() || url.GetScheme() != content::kExternalFileScheme) {
    return base::FilePath();
  }
  return base::FilePath::FromUTF8Unsafe(
      base::UnescapeBinaryURLComponent(url.path()));
}
```
