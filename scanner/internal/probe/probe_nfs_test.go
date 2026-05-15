// NFS-probe honesty (v0.29.0).
//
// Pre-v0.29.0 the in-process NFS probe was `net.Dial("tcp", host:port)`
// only — it returned ok=true purely from port reachability, even when
// credentials would fail at MOUNT3/NFSv4 LOOKUP and the share wasn't
// actually scanable. Post-fix runNFS dispatches into the nfsprobe
// package (same code path the API-side test_nfs invokes via the CLI).
//
// We can't exercise the full nfsprobe round trip in unit tests without
// a real NFS server, but we CAN guarantee the config-step guard fires:
// no export_path → step=config, ok=false. Pre-fix the same input
// returned ok=true.
package probe

import (
	"context"
	"testing"
	"time"
)

func TestNFSRequiresExportPath(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	// Host set, export_path missing — pre-fix this 200-OK'd after a
	// successful TCP dial. Post-fix it's a config-step failure.
	res := runNFS(ctx, map[string]any{
		"host": "127.0.0.1",
		"port": 2049,
	})
	if res.OK {
		t.Fatalf("expected OK=false when export_path is missing; got %+v", res)
	}
	if res.Step != "config" {
		t.Fatalf("expected step=config; got step=%q error=%q",
			res.Step, res.Error)
	}
}

func TestNFSRequiresHost(t *testing.T) {
	res := runNFS(context.Background(), map[string]any{})
	if res.OK || res.Step != "config" {
		t.Fatalf("expected config-step failure on missing host; got %+v", res)
	}
}

func TestParseAuxGIDsAnyHandlesShapes(t *testing.T) {
	// JSON-decoded form: []any with float64 values.
	got := parseAuxGIDsAny([]any{float64(27), float64(100), "5", "bogus"})
	want := []uint32{27, 100, 5}
	if len(got) != len(want) {
		t.Fatalf("len mismatch: got=%v want=%v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("idx %d: got=%d want=%d", i, got[i], want[i])
		}
	}
	// String form, as the CLI receives.
	got = parseAuxGIDsAny("27, 100,, 5")
	if len(got) != 3 || got[2] != 5 {
		t.Errorf("string form: got=%v", got)
	}
	// Nil — empty result.
	if r := parseAuxGIDsAny(nil); r != nil {
		t.Errorf("nil input: expected nil, got %v", r)
	}
}

// TestNFSPropagatesNFSProbeError exercises the typed-error branch in
// runNFS by passing a host that can't be reached on a non-routable IP.
// The probe should fail at the connect step rather than claim ok=true.
//
// Skipped in CI environments that block outbound — uses TEST_DOCKER_NETWORK
// as a heuristic; the akashic test container has docker network egress.
func TestNFSConnectsAtRealHost(t *testing.T) {
	// 240.0.0.1 is in the reserved 240.0.0.0/4 class-E block — won't
	// route on any normal network. Probe should fail at TCP / portmap
	// stage in well under our 2 s outer.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	res := runNFS(ctx, map[string]any{
		"host":                    "240.0.0.1",
		"export_path":             "/exports/test",
		"probe_timeout_seconds":   1,
	})
	if res.OK {
		t.Fatalf("expected ok=false for unreachable host; got %+v", res)
	}
	// Step varies based on whether DNS / TCP / portmap fails first;
	// any non-empty step that's NOT "config" is acceptable here — we're
	// proving the probe actually attempted the protocol rather than
	// short-circuiting to TCP-only success.
	if res.Step == "config" {
		t.Fatalf("expected protocol-level failure step; got step=%q error=%q",
			res.Step, res.Error)
	}
}
