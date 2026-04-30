package nfsprobe

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// probeKrb5 validation paths exercise without touching the network: bad
// AuthMethod values, missing principal/realm, missing keytab AND
// password. These paths short-circuit before any TCP/KDC traffic.

func TestProbeKrb5RejectsKrb5IAndKrb5P(t *testing.T) {
	cases := []AuthMethod{AuthKrb5Integrity, AuthKrb5Privacy}
	for _, m := range cases {
		t.Run(string(m), func(t *testing.T) {
			_, err := Probe(context.Background(), ProbeOptions{
				Host:       "h",
				ExportPath: "/e",
				AuthMethod: m,
			})
			var pe *ProbeError
			if !errors.As(err, &pe) {
				t.Fatalf("want *ProbeError, got %T (%v)", err, err)
			}
			if pe.Step != StepConfig {
				t.Errorf("step: want config, got %s", pe.Step)
			}
			if !strings.Contains(pe.Msg, "not supported") {
				t.Errorf("expected 'not supported' in msg, got %q", pe.Msg)
			}
		})
	}
}

func TestProbeKrb5RequiresPrincipalRealm(t *testing.T) {
	_, err := Probe(context.Background(), ProbeOptions{
		Host:       "h",
		ExportPath: "/e",
		AuthMethod: AuthKrb5,
	})
	var pe *ProbeError
	if !errors.As(err, &pe) {
		t.Fatalf("want *ProbeError, got %T", err)
	}
	if pe.Step != StepConfig {
		t.Errorf("step: want config, got %s", pe.Step)
	}
	if !strings.Contains(pe.Msg, "principal") {
		t.Errorf("expected principal/realm error, got %q", pe.Msg)
	}
}

func TestProbeKrb5RequiresKeytabOrPassword(t *testing.T) {
	_, err := Probe(context.Background(), ProbeOptions{
		Host:          "h",
		ExportPath:    "/e",
		AuthMethod:    AuthKrb5,
		Krb5Principal: "alice",
		Krb5Realm:     "EXAMPLE.COM",
	})
	var pe *ProbeError
	if !errors.As(err, &pe) {
		t.Fatalf("want *ProbeError, got %T", err)
	}
	if pe.Step != StepConfig {
		t.Errorf("step: want config, got %s", pe.Step)
	}
	if !strings.Contains(pe.Msg, "keytab_path or password") {
		t.Errorf("expected keytab/password error, got %q", pe.Msg)
	}
}

// isKerberos catches all three flavors. AuthSys and the empty string
// (defaults to sys) must fall through to the AUTH_SYS cascade.
func TestIsKerberos(t *testing.T) {
	cases := map[AuthMethod]bool{
		AuthSys:           false,
		"":                false,
		AuthKrb5:          true,
		AuthKrb5Integrity: true,
		AuthKrb5Privacy:   true,
		"unknown":         false,
	}
	for m, want := range cases {
		if got := isKerberos(m); got != want {
			t.Errorf("isKerberos(%q): want %v, got %v", m, want, got)
		}
	}
}
