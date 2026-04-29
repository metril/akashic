import { getToken } from "../api/client";

/**
 * Fetches /api/entries/{id}/content as a Blob and triggers a browser
 * download. Avoids the api client's JSON parser and keeps the auth-token
 * handling consistent with the rest of the app.
 */
export async function downloadEntryContent(
  entryId: string,
  filename: string,
): Promise<void> {
  const token = getToken();
  const response = await fetch(`/api/entries/${entryId}/content?attachment=1`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new Error(`Download failed: HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer revocation: a.click() dispatches the download asynchronously,
  // so the browser may not have read the blob URL yet when this function
  // returns. Revoking immediately aborts the download in Firefox.
  setTimeout(() => URL.revokeObjectURL(url), 100);
}
