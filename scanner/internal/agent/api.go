package agent

import (
	"context"
	"crypto/ed25519"
	"fmt"
	"net/http"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// newAPI returns the typed API client for scanner-signed calls. Its request
// editor mints a fresh JWT per request. Each token carries a unique `jti`
// (random 16-byte UUID, see MintJWT); the API enforces one-time JTI replay
// protection in services/scanner_jti.py, so reusing a cached token would
// 401-replay every call after the first.
//
// The pre-v0.27.1 implementation cached one signed header for ~4 min
// to "save a few microseconds per call" — but Ed25519 signatures are
// genuinely microsecond-cheap and the cache silently broke the
// replay guard. Lease/heartbeat/reachability all serialise the same
// jti, so only the first call per cache cycle succeeded; everything
// after returned 401 and the agent claimed no work.
func newAPI(httpc *http.Client, cfg Config, priv ed25519.PrivateKey) (*apiclient.ClientWithResponses, error) {
	return apiclient.NewClientWithResponses(cfg.APIBase,
		apiclient.WithHTTPClient(httpc),
		apiclient.WithRequestEditorFn(SignEditor(priv, cfg.ScannerID.String())),
	)
}

// SignEditor authenticates a request as the scanner: a fresh EdDSA JWT (new
// jti) in the Authorization header on every call.
func SignEditor(priv ed25519.PrivateKey, scannerID string) apiclient.RequestEditorFn {
	return func(_ context.Context, req *http.Request) error {
		tok, err := MintJWT(priv, scannerID)
		if err != nil {
			return fmt.Errorf("sign request: %w", err)
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		return nil
	}
}

// httpError describes an unexpected API status together with the raw body.
func httpError(what string, status int, body []byte) error {
	return fmt.Errorf("%s HTTP %d: %s", what, status, string(body))
}

// detailString reads a string field out of an error envelope's `detail`
// object ({status, reason, message}); "" when absent or not an object.
func detailString(e *apiclient.ErrorResponse, key string) string {
	if e == nil {
		return ""
	}
	m, ok := e.Detail.(map[string]any)
	if !ok {
		return ""
	}
	s, _ := m[key].(string)
	return s
}
