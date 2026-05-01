// Package agent implements the long-running scanner agent that polls
// the api for leased scans and runs them. Ed25519 + JWT auth: the
// agent's private key never leaves disk; every api call carries a
// freshly-minted, short-lived JWT signed with that key.
package agent

import (
	"crypto/ed25519"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"os"
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
