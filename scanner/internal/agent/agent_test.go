package agent

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"crypto/x509"
	"encoding/pem"
)

// writePEMKey serialises an Ed25519 private key as PKCS8 PEM and
// returns the path. Matches what the api's scanner_keys module
// produces, so the agent's LoadPrivateKey can read it back.
func writePEMKey(t *testing.T, priv ed25519.PrivateKey) string {
	t.Helper()
	der, err := x509.MarshalPKCS8PrivateKey(priv)
	if err != nil {
		t.Fatal(err)
	}
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	path := filepath.Join(t.TempDir(), "scanner.key")
	if err := os.WriteFile(path, pemBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestLoadPrivateKey_RoundTrip(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	path := writePEMKey(t, priv)
	loaded, err := LoadPrivateKey(path)
	if err != nil {
		t.Fatalf("LoadPrivateKey: %v", err)
	}
	if !priv.Equal(loaded) {
		t.Error("loaded key does not match original")
	}
}

func TestMintJWT_HasExpectedShape(t *testing.T) {
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	tok, err := MintJWT(priv, "abc-123")
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(tok, ".")
	if len(parts) != 3 {
		t.Fatalf("expected 3 segments, got %d", len(parts))
	}
	// Header decodes and contains alg=EdDSA + kid.
	hdrBytes, err := decodeBase64URL(parts[0])
	if err != nil {
		t.Fatal(err)
	}
	var header map[string]string
	if err := json.Unmarshal(hdrBytes, &header); err != nil {
		t.Fatal(err)
	}
	if header["alg"] != "EdDSA" {
		t.Errorf("alg=%s, want EdDSA", header["alg"])
	}
	if header["kid"] != "abc-123" {
		t.Errorf("kid=%s, want abc-123", header["kid"])
	}
}

// TestAgentLeaseLoop_HandlesEmptyLeasesAndReturns204
//
// Stand up a fake api that:
//   - accepts the handshake with 200 OK
//   - returns 204 on /api/scans/lease (no work)
//
// The agent should poll, see 204, sleep, and be cancellable by the
// caller's context.
func TestAgentLeaseLoop_HandlesEmptyLeases(t *testing.T) {
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	keyPath := writePEMKey(t, priv)

	leases := 0
	var sawAgentVersion string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/scanners/handshake" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"accepted": true, "server_protocol_version": 1,
				"accepted_min": 1, "accepted_max": 1,
			})
			return
		}
		if r.URL.Path == "/api/scans/lease" {
			leases++
			// v0.30.2 — the lease body reports the running build so
			// the API can keep scanners.version fresh.
			var body struct {
				AgentVersion string `json:"agent_version"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.AgentVersion != "" {
				sawAgentVersion = body.AgentVersion
			}
			w.WriteHeader(http.StatusNoContent)
			return
		}
		// heartbeat or unknown — just 204
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	cfg := Config{
		APIBase:   srv.URL,
		ScannerID: "test-scanner",
		KeyPath:   keyPath,
		LeasePoll: 50 * time.Millisecond,
		Hostname:  "host",
		Version:   "test",
	}

	ctx, cancel := newCancelCtx()
	defer cancel()

	go func() {
		time.Sleep(250 * time.Millisecond)
		cancel()
	}()

	if err := Run(ctx, cfg); err != nil {
		t.Errorf("Run returned error after cancel: %v", err)
	}
	if leases == 0 {
		t.Error("expected at least one /lease call before cancel")
	}
	if sawAgentVersion != "test" {
		t.Errorf("lease request agent_version = %q, want %q (cfg.Version)",
			sawAgentVersion, "test")
	}
}

// TestAuthHeader_MintsFreshJWTPerCall is the regression for v0.27.1.
// Pre-fix authHeader cached one signed JWT for ~4 minutes and reused
// it across calls — but the API enforces one-time JTI replay
// protection (services/scanner_jti.py), so every call after the first
// returned 401 "token replay detected" and the agent claimed no work.
// Fix: mint a fresh JWT per call (each gets a unique jti via
// MintJWT). This test asserts two consecutive calls produce
// different headers (different jti → different signature).
func TestAuthHeader_MintsFreshJWTPerCall(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	cfg := Config{ScannerID: "scanner-x"}

	first, err := authHeader(cfg, priv)
	if err != nil {
		t.Fatal(err)
	}
	second, err := authHeader(cfg, priv)
	if err != nil {
		t.Fatal(err)
	}
	if first == "" {
		t.Fatal("empty header")
	}
	if first == second {
		t.Fatalf("expected fresh JWT per call (unique jti); got same header reused — cache regressed")
	}

	// Confirm the jti claims actually differ — that's the security
	// property we depend on.
	jti1 := jtiFromBearer(t, first)
	jti2 := jtiFromBearer(t, second)
	if jti1 == jti2 {
		t.Fatalf("expected distinct jti claims, got %q both times", jti1)
	}
}

// jtiFromBearer decodes the claims of a "Bearer xxx.yyy.zzz" header
// and returns the jti. Test helper — bails the test on any decode
// failure.
func jtiFromBearer(t *testing.T, header string) string {
	t.Helper()
	tok := strings.TrimPrefix(header, "Bearer ")
	parts := strings.Split(tok, ".")
	if len(parts) != 3 {
		t.Fatalf("malformed bearer: %q", header)
	}
	body, err := decodeBase64URL(parts[1])
	if err != nil {
		t.Fatalf("decode claims: %v", err)
	}
	var claims struct {
		JTI string `json:"jti"`
	}
	if err := json.Unmarshal(body, &claims); err != nil {
		t.Fatalf("unmarshal claims: %v", err)
	}
	return claims.JTI
}

func TestAgentHandshake_RejectsOutOfRangeProtocol(t *testing.T) {
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	keyPath := writePEMKey(t, priv)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/scanners/handshake" {
			w.WriteHeader(http.StatusUpgradeRequired)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"accepted": false, "server_protocol_version": 2,
				"accepted_min": 2, "accepted_max": 2,
				"reason": "agent too old",
			})
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	cfg := Config{
		APIBase:   srv.URL,
		ScannerID: "test-scanner",
		KeyPath:   keyPath,
		LeasePoll: 50 * time.Millisecond,
	}
	ctx, cancel := newCancelCtx()
	defer cancel()
	if err := Run(ctx, cfg); err == nil {
		t.Error("expected handshake error, got nil")
	} else if !strings.Contains(err.Error(), "rejected protocol_version") {
		t.Errorf("unexpected error: %v", err)
	}
}

// — small helpers —

func newCancelCtx() (ctxlike, func()) {
	return newContextHelper()
}

// v0.29.10 — terminalDisposition must NOT post /complete when the API
// already reported the scan terminal via a heartbeat 409. Pre-fix the
// agent always posted "cancelled" on that path, clobbering a watchdog
// "failed" or a sibling scanner's "completed".
func TestTerminalDisposition(t *testing.T) {
	cases := []struct {
		name       string
		runErr     error
		wantStatus string
		wantPost   bool
	}{
		{
			name:       "clean finish completes the scan",
			runErr:     nil,
			wantStatus: "completed",
			wantPost:   true,
		},
		{
			name:       "API-terminated scan posts nothing",
			runErr:     errAPITerminated,
			wantStatus: "",
			wantPost:   false,
		},
		{
			name:       "wrapped API-terminated still posts nothing",
			runErr:     fmt.Errorf("run aborted: %w", errAPITerminated),
			wantStatus: "",
			wantPost:   false,
		},
		{
			name:       "genuine error fails the scan",
			runErr:     errors.New("connector dial timeout"),
			wantStatus: "failed",
			wantPost:   true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			status, post := terminalDisposition(tc.runErr)
			if status != tc.wantStatus {
				t.Errorf("status = %q, want %q", status, tc.wantStatus)
			}
			if post != tc.wantPost {
				t.Errorf("postComplete = %v, want %v", post, tc.wantPost)
			}
		})
	}
}

// v0.36.0 — the scanner self-reports its MaxConcurrentUnits on
// handshake so the admin UI can show each scanner's effective
// concurrency. omitempty keeps an unset value off the wire so an older
// API silently ignores it instead of decoding to 0.
func TestHandshakeReq_EncodesMaxConcurrentUnits(t *testing.T) {
	body, err := json.Marshal(handshakeReq{
		ProtocolVersion:    1,
		Version:            "v",
		Hostname:           "h",
		MaxConcurrentUnits: 3,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), `"max_concurrent_units":3`) {
		t.Errorf("body=%s, want max_concurrent_units:3", body)
	}
	body2, err := json.Marshal(handshakeReq{ProtocolVersion: 1})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body2), "max_concurrent_units") {
		t.Errorf("body=%s, expected max_concurrent_units omitted when 0", body2)
	}
}
