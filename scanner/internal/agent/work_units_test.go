// Work-unit client coverage (v0.34.0).
//
// leaseUnit must tell three 4xx/2xx outcomes apart: 204 (no unit right
// now — keep polling), 409 scan-terminal (the scan finished — exit), and
// 409 cap (units exist, no slot — retry). failUnit must carry the
// requeue flag so a transient stall retries the unit rather than
// abandoning its subtree.
package agent

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func leaseTestCfg(t *testing.T, url string) (Config, ed25519.PrivateKey) {
	t.Helper()
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return Config{APIBase: url, ScannerID: "scanner-1"}, priv
}

func TestLeaseUnit_ScanTerminal409_MapsToErrScanTerminal(t *testing.T) {
	// 409 whose detail.reason is "scan-terminal" → the scan is over.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(
			`{"detail":{"status":"completed","reason":"scan-terminal","message":"done"}}`))
	}))
	defer srv.Close()

	cfg, priv := leaseTestCfg(t, srv.URL)
	_, err := leaseUnit(context.Background(), srv.Client(), cfg, priv, "scan-1")
	if !errors.Is(err, errScanTerminal) {
		t.Fatalf("scan-terminal 409 should map to errScanTerminal, got %v", err)
	}
	if errors.Is(err, errLeaseCap) {
		t.Error("scan-terminal 409 must NOT be treated as a lease cap")
	}
}

func TestLeaseUnit_Cap409_MapsToErrLeaseCap(t *testing.T) {
	// 409 whose detail is a plain string (the max_parallel_scanners cap)
	// → units exist, just no slot. Must stay errLeaseCap, not terminal.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"detail":"max_parallel_scanners cap (2) reached"}`))
	}))
	defer srv.Close()

	cfg, priv := leaseTestCfg(t, srv.URL)
	_, err := leaseUnit(context.Background(), srv.Client(), cfg, priv, "scan-1")
	if !errors.Is(err, errLeaseCap) {
		t.Fatalf("cap 409 should map to errLeaseCap, got %v", err)
	}
	if errors.Is(err, errScanTerminal) {
		t.Error("a cap 409 must NOT be treated as scan-terminal")
	}
}

func TestLeaseUnit_204_MapsToErrNoWork(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	cfg, priv := leaseTestCfg(t, srv.URL)
	_, err := leaseUnit(context.Background(), srv.Client(), cfg, priv, "scan-1")
	if !errors.Is(err, errNoWork) {
		t.Fatalf("204 should map to errNoWork, got %v", err)
	}
}

func TestFailUnit_CarriesRequeueFlag(t *testing.T) {
	for _, requeue := range []bool{true, false} {
		var gotRequeue bool
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var body struct {
				Requeue bool `json:"requeue"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			gotRequeue = body.Requeue
			w.WriteHeader(http.StatusNoContent)
		}))

		cfg, priv := leaseTestCfg(t, srv.URL)
		err := failUnit(context.Background(), srv.Client(), cfg, priv,
			"scan-1", "unit-1", "smb stalled", requeue)
		srv.Close()
		if err != nil {
			t.Fatalf("failUnit(requeue=%v): %v", requeue, err)
		}
		if gotRequeue != requeue {
			t.Errorf("failUnit(requeue=%v): server received requeue=%v", requeue, gotRequeue)
		}
	}
}
