package nfsprobe

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/jcmturner/gokrb5/v8/client"
	"github.com/jcmturner/gokrb5/v8/config"
	"github.com/jcmturner/gokrb5/v8/keytab"
	"github.com/jcmturner/gokrb5/v8/messages"
	"github.com/jcmturner/gokrb5/v8/spnego"
	"github.com/jcmturner/gokrb5/v8/types"
)

// krb5Client wraps gokrb5's *client.Client with the small API surface the
// probe needs:
//
//   1. login (TGT acquisition) from either keytab or password
//   2. service ticket for nfs/<host>@REALM
//   3. an AP_REQ GSS-API token suitable for RPCSEC_GSS_INIT
//
// We deliberately avoid SPNEGO mech-negotiation: NFS exclusively
// uses the raw Kerberos OID (1.2.840.113554.1.2.2). Calling
// spnego.NewKRB5TokenAPREQ produces exactly that wire shape.
type krb5Client struct {
	cl  *client.Client
	spn string

	// Cached after acquireServiceTicket.
	ticket     messages.Ticket
	sessionKey types.EncryptionKey
}

// krb5Options carries the per-source kerberos config from the CLI.
type krb5Options struct {
	// Username (without realm); e.g. "jdoe" or "akashic-svc".
	Principal string
	// Realm; uppercase by convention; e.g. "EXAMPLE.COM".
	Realm string
	// Service Principal Name; if empty, defaults to "nfs/<host>".
	ServicePrincipal string
	// Path to a keytab file. Mutually exclusive with Password.
	KeytabPath string
	// Password (provided via stdin from the API). Mutually exclusive
	// with KeytabPath.
	Password string
	// Path to an alternate krb5.conf. If empty, /etc/krb5.conf is used
	// when present; otherwise we synthesize a minimal config from Realm
	// + a list of KDCs derived from Realm DNS.
	ConfigPath string
}

// newKrb5Client builds and logs in a gokrb5 client. Any failure here is
// fatal for the probe: without a TGT we cannot proceed.
func newKrb5Client(host string, opts krb5Options) (*krb5Client, error) {
	if opts.Principal == "" {
		return nil, errors.New("krb5: principal required")
	}
	if opts.Realm == "" {
		return nil, errors.New("krb5: realm required")
	}
	cfg, err := loadKrb5Config(opts.ConfigPath, opts.Realm)
	if err != nil {
		return nil, fmt.Errorf("krb5: load config: %w", err)
	}

	// disable PA-FX-FAST so we work against KDCs that don't advertise it
	// (most non-AD KDCs, including older MIT setups). Enabling FAST when
	// the KDC doesn't support it is a hard failure on AS_REQ — disabling
	// it is the safe default for a probe.
	settings := []func(*client.Settings){
		client.DisablePAFXFAST(true),
	}

	var cl *client.Client
	switch {
	case opts.KeytabPath != "" && opts.Password != "":
		return nil, errors.New("krb5: keytab and password are mutually exclusive")
	case opts.KeytabPath != "":
		kt, err := keytab.Load(opts.KeytabPath)
		if err != nil {
			return nil, fmt.Errorf("krb5: load keytab %q: %w", opts.KeytabPath, err)
		}
		cl = client.NewWithKeytab(opts.Principal, opts.Realm, kt, cfg, settings...)
	case opts.Password != "":
		cl = client.NewWithPassword(opts.Principal, opts.Realm, opts.Password, cfg, settings...)
	default:
		return nil, errors.New("krb5: either keytab_path or password is required")
	}

	if err := cl.Login(); err != nil {
		return nil, fmt.Errorf("krb5: login (AS_REQ): %w", err)
	}

	spn := opts.ServicePrincipal
	if spn == "" {
		// "nfs/host" form (no @REALM). gokrb5 derives the realm from the
		// service hostname or falls back to the client's realm via the
		// libdefaults.
		spn = "nfs/" + canonicalHost(host)
	}

	return &krb5Client{cl: cl, spn: spn}, nil
}

// acquireServiceTicket runs TGS_REQ for nfs/<host>@REALM. The returned
// ticket+key are cached so the probe can issue multiple calls within
// the same context establishment without re-asking the KDC.
func (k *krb5Client) acquireServiceTicket() error {
	if k.sessionKey.KeyValue != nil {
		return nil
	}
	tkt, key, err := k.cl.GetServiceTicket(k.spn)
	if err != nil {
		return fmt.Errorf("krb5: TGS_REQ for %q: %w", k.spn, err)
	}
	k.ticket = tkt
	k.sessionKey = key
	return nil
}

// buildAPReqToken builds the GSS-API mechanism token wrapping an AP_REQ
// for the cached service ticket. The bytes returned go directly into
// the rpc_gss_init_arg.gss_token field.
//
// flags is the GSS context-flag bitmap (mutual auth, sequence,
// integrity/conf as needed). For NFS the kernel client typically asks
// for sequence + integrity (and confidentiality for krb5p) via the
// authenticator checksum.
func (k *krb5Client) buildAPReqToken(flags []int) ([]byte, error) {
	if err := k.acquireServiceTicket(); err != nil {
		return nil, err
	}
	tok, err := spnego.NewKRB5TokenAPREQ(k.cl, k.ticket, k.sessionKey, flags, nil)
	if err != nil {
		return nil, fmt.Errorf("krb5: build AP_REQ: %w", err)
	}
	b, err := tok.Marshal()
	if err != nil {
		return nil, fmt.Errorf("krb5: marshal AP_REQ token: %w", err)
	}
	return b, nil
}

// canonicalHost returns the host as the Kerberos service principal
// expects it: lowercased, no port. Most KDCs are case-sensitive on the
// service-instance component.
func canonicalHost(h string) string {
	h = strings.ToLower(h)
	if i := strings.Index(h, ":"); i >= 0 {
		h = h[:i]
	}
	return h
}

// loadKrb5Config tries the explicit path first, then /etc/krb5.conf,
// then falls back to a synthesized config that just declares the realm
// and lets gokrb5's DNS-based KDC discovery do the rest.
func loadKrb5Config(path, realm string) (*config.Config, error) {
	if path != "" {
		return config.Load(path)
	}
	// Default location.
	if _, err := os.Stat("/etc/krb5.conf"); err == nil {
		return config.Load("/etc/krb5.conf")
	}
	// Synthesize a minimal config. gokrb5 will resolve the KDC via DNS
	// SRV records (_kerberos._udp.REALM) — works against AD and modern
	// MIT KDCs that publish SRVs. If that lookup fails the user should
	// supply an explicit krb5.conf via kdc_config_path.
	return config.NewFromString(fmt.Sprintf(`
[libdefaults]
  default_realm = %s
  dns_lookup_kdc = true
  dns_lookup_realm = false
  rdns = false

[realms]
  %s = {
  }
`, realm, realm))
}
