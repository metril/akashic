package agent

import (
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGenerateKeypair_ParsesAsEd25519(t *testing.T) {
	priv, pub, err := GenerateKeypair()
	if err != nil {
		t.Fatalf("GenerateKeypair: %v", err)
	}
	if !strings.Contains(priv, "PRIVATE KEY") {
		t.Errorf("private PEM missing header: %q", priv)
	}
	if !strings.Contains(pub, "PUBLIC KEY") {
		t.Errorf("public PEM missing header: %q", pub)
	}
	block, _ := pem.Decode([]byte(priv))
	if block == nil {
		t.Fatalf("private PEM didn't decode")
	}
	if _, err := x509.ParsePKCS8PrivateKey(block.Bytes); err != nil {
		t.Fatalf("private key isn't valid PKCS8: %v", err)
	}
	pubBlock, _ := pem.Decode([]byte(pub))
	if pubBlock == nil {
		t.Fatalf("public PEM didn't decode")
	}
	if _, err := x509.ParsePKIXPublicKey(pubBlock.Bytes); err != nil {
		t.Fatalf("public key isn't valid PKIX: %v", err)
	}
}

func TestWritePrivateKey_FailsIfFileExists(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "scanner.key")
	if err := os.WriteFile(path, []byte("existing"), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	err := WritePrivateKey(path, "new-content")
	if err == nil {
		t.Fatal("expected error when overwriting existing key")
	}
	if !strings.Contains(err.Error(), "refusing to overwrite") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestWritePrivateKey_WritesWithMode0600(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "subdir", "scanner.key")
	priv, _, err := GenerateKeypair()
	if err != nil {
		t.Fatalf("GenerateKeypair: %v", err)
	}
	if err := WritePrivateKey(path, priv); err != nil {
		t.Fatalf("WritePrivateKey: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("expected mode 0600, got %v", info.Mode().Perm())
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(got) != priv {
		t.Errorf("content mismatch")
	}
}

func TestWriteScannerID_Roundtrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "scanner.id")
	if err := WriteScannerID(path, "abc-123"); err != nil {
		t.Fatalf("WriteScannerID: %v", err)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(got) != "abc-123" {
		t.Errorf("got %q, want abc-123", string(got))
	}
}
