/**
 * Open an OAuth authorize URL in a popup window and resolve when the
 * callback HTML posts a message back via ``window.opener.postMessage``.
 *
 * Pairs with ``api/akashic/routers/source_oauth.callback`` — that
 * route renders an HTML page whose inline script posts a payload of:
 *
 *   { akashic_oauth: true, ok: true, mode: "associate" | "test",
 *     provider: string, account_email: string, credential_id?: string }
 *
 * or, on failure:
 *
 *   { akashic_oauth: true, ok: false, error: string }
 *
 * The promise rejects if the popup is blocked, manually closed before
 * sending a message, or sends an error payload.
 */

export interface OAuthPopupResultOk {
  ok: true;
  mode: "associate" | "test";
  provider: string;
  account_email: string;
  credential_id?: string;
}

export interface OAuthPopupResultErr {
  ok: false;
  error: string;
}

export type OAuthPopupResult = OAuthPopupResultOk | OAuthPopupResultErr;

const POPUP_FEATURES = "popup,width=560,height=720,resizable,scrollbars";
const POLL_MS = 500;
const TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes — matches state token TTL

export function openOAuthPopup(authorizationUrl: string): Promise<OAuthPopupResult> {
  return new Promise<OAuthPopupResult>((resolve, reject) => {
    const popup = window.open(authorizationUrl, "akashic-oauth", POPUP_FEATURES);
    if (!popup) {
      reject(
        new Error(
          "OAuth popup was blocked. Allow popups for this site and try again.",
        ),
      );
      return;
    }

    let settled = false;

    const cleanup = () => {
      window.removeEventListener("message", onMessage);
      window.clearInterval(pollTimer);
      window.clearTimeout(timeoutTimer);
    };

    const finish = (
      result: OAuthPopupResult,
      err: Error | null,
    ) => {
      if (settled) return;
      settled = true;
      cleanup();
      try {
        if (!popup.closed) popup.close();
      } catch {
        /* ignore — cross-origin closed-state errors after window.close */
      }
      if (err) reject(err);
      else resolve(result);
    };

    const onMessage = (e: MessageEvent) => {
      // Same-origin only — the callback HTML is served from the API
      // origin. Reject any message from elsewhere.
      if (e.origin !== window.location.origin) return;
      const data = e.data as Record<string, unknown> | null;
      if (!data || data.akashic_oauth !== true) return;

      if (data.ok === true) {
        finish(
          {
            ok: true,
            mode: (data.mode as "associate" | "test") ?? "associate",
            provider: String(data.provider ?? ""),
            account_email: String(data.account_email ?? ""),
            credential_id: data.credential_id
              ? String(data.credential_id)
              : undefined,
          },
          null,
        );
      } else {
        finish(
          { ok: false, error: String(data.error ?? "unknown_error") },
          null,
        );
      }
    };

    window.addEventListener("message", onMessage);

    const pollTimer = window.setInterval(() => {
      // The popup closing without a message is the user-cancelled case.
      // We can't read popup.location across origins, so the closed flag
      // is the only sane signal.
      if (popup.closed) {
        finish({ ok: false, error: "popup_closed" }, null);
      }
    }, POLL_MS);

    const timeoutTimer = window.setTimeout(() => {
      finish({ ok: false, error: "timeout" }, null);
    }, TIMEOUT_MS);
  });
}
