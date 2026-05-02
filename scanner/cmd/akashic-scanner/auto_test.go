package main

import (
	"os"
	"path/filepath"
	"testing"
)

// These tests cover the small pure helpers that drive the priority
// branching in `auto`. The branch dispatch itself ends in
// syscall.Exec / log.Fatal, neither of which is unit-testable
// without spawning subprocesses — that path is covered by the
// release pipeline's end-to-end smoke instead.

func TestIsTruthy(t *testing.T) {
	cases := map[string]bool{
		"1": true, "true": true, "TRUE": true, "yes": true, "on": true,
		"0": false, "false": false, "no": false, "": false, "garbage": false,
		" 1 ": true,
	}
	for in, want := range cases {
		if got := isTruthy(in); got != want {
			t.Errorf("isTruthy(%q) = %v, want %v", in, got, want)
		}
	}
}

func TestEnvOr(t *testing.T) {
	t.Setenv("AKASHIC_TEST_FOO", "")
	if got := envOr("AKASHIC_TEST_FOO", "default"); got != "default" {
		t.Errorf("expected default for unset, got %q", got)
	}
	t.Setenv("AKASHIC_TEST_FOO", "value")
	if got := envOr("AKASHIC_TEST_FOO", "default"); got != "value" {
		t.Errorf("expected value, got %q", got)
	}
}

func TestReadScannerID_TrimsAndHandlesMissing(t *testing.T) {
	dir := t.TempDir()
	miss := filepath.Join(dir, "absent.id")
	if got := readScannerID(miss); got != "" {
		t.Errorf("expected empty for missing file, got %q", got)
	}
	full := filepath.Join(dir, "scanner.id")
	if err := os.WriteFile(full, []byte("  abc-123\n"), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	if got := readScannerID(full); got != "abc-123" {
		t.Errorf("expected trimmed 'abc-123', got %q", got)
	}
}

func TestFileExists(t *testing.T) {
	dir := t.TempDir()
	miss := filepath.Join(dir, "nope")
	if fileExists(miss) {
		t.Errorf("missing file reported as existing")
	}
	there := filepath.Join(dir, "there")
	if err := os.WriteFile(there, nil, 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	if !fileExists(there) {
		t.Errorf("present file reported as missing")
	}
}
