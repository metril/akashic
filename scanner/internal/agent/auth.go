// Package agent implements the long-running scanner agent that polls
// the api for leased scans and runs them. Ed25519 + JWT auth: the
// agent's private key never leaves disk; every api call carries a
// freshly-minted, short-lived JWT signed with that key.
package agent

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// LoadPrivateKey reads a PEM-encoded PKCS8 Ed25519 private key from
// disk. Matches the format produced by the api's
// services/scanner_keys.generate_keypair (cryptography's
// PrivateFormat.PKCS8 + Encoding.PEM).
func LoadPrivateKey(path string) (ed25519.PrivateKey, error) {
	pemBytes, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read key %s: %w", path, err)
	}
	block, _ := pem.Decode(pemBytes)
	if block == nil {
		return nil, fmt.Errorf("no PEM block in %s", path)
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse PKCS8 in %s: %w", path, err)
	}
	priv, ok := parsed.(ed25519.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("expected Ed25519 key, got %T", parsed)
	}
	return priv, nil
}

// MintJWT produces an EdDSA JWT for the given scanner identity. The
// claims layout matches what api/akashic/services/scanner_auth expects:
// iss=scanner, sub=scanner_id, kid header = scanner_id, 5-minute exp.
func MintJWT(priv ed25519.PrivateKey, scannerID string) (string, error) {
	now := time.Now().Unix()
	header := map[string]string{
		"alg": "EdDSA",
		"typ": "JWT",
		"kid": scannerID,
	}
	claims := map[string]any{
		"iss": "scanner",
		"sub": scannerID,
		"iat": now,
		"exp": now + 300, // 5 minutes
	}
	hb, err := json.Marshal(header)
	if err != nil {
		return "", err
	}
	cb, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	h := base64URL(hb)
	c := base64URL(cb)
	signing := []byte(h + "." + c)
	sig := ed25519.Sign(priv, signing)
	return h + "." + c + "." + base64URL(sig), nil
}

func base64URL(b []byte) string {
	return base64.RawURLEncoding.EncodeToString(b)
}

// GenerateKeypair makes a fresh Ed25519 pair locally on the scanner
// host and serialises it the same way the api side does (PKCS8 PEM
// for the private key, SubjectPublicKeyInfo PEM for the public). The
// PEMs are byte-compatible with the api's load_pem_* path, so the
// fingerprint matches on both sides without further normalisation.
func GenerateKeypair() (privPEM, pubPEM string, err error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", "", fmt.Errorf("generate ed25519: %w", err)
	}
	privDER, err := x509.MarshalPKCS8PrivateKey(priv)
	if err != nil {
		return "", "", fmt.Errorf("marshal pkcs8: %w", err)
	}
	pubDER, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		return "", "", fmt.Errorf("marshal pkix pub: %w", err)
	}
	privPEM = string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privDER}))
	pubPEM = string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubDER}))
	return privPEM, pubPEM, nil
}

// WritePrivateKey persists the private key PEM to `path` with mode
// 0600. Refuses to overwrite an existing file — losing a key on
// re-claim is an unrecoverable footgun.
func WritePrivateKey(path, privPEM string) error {
	if _, err := os.Stat(path); err == nil {
		return fmt.Errorf("refusing to overwrite existing key at %s", path)
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("stat %s: %w", path, err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("mkdir parents of %s: %w", path, err)
	}
	if err := os.WriteFile(path, []byte(privPEM), 0o600); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}

// WriteScannerID persists the scanner UUID to `path`. Single line,
// no trailing newline so `cat $path` is paste-friendly.
func WriteScannerID(path, scannerID string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("mkdir parents of %s: %w", path, err)
	}
	if err := os.WriteFile(path, []byte(scannerID), 0o600); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}
