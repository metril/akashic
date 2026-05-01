package agent

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
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
}

// TestAgentHandshake_RejectsOutOfRangeProtocol confirms the agent
// surfaces the api's 426 as a fatal startup error rather than looping.
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
