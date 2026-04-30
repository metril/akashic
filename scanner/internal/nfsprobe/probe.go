package nfsprobe

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"
)

// Public types — the scanner's CLI consumes these.

// AuthMethod selects how the probe authenticates to MOUNT3 MNT and
// NFSv4 LOOKUP. AUTH_NONE (no identity asserted) is rare for those
// procs and defaults to AUTH_SYS instead.
type AuthMethod string

const (
	AuthSys AuthMethod = "sys"
	// Krb5 — RPCSEC_GSS with service=NONE: client identity proven via
	// the AP_REQ exchanged at context-init; per-call MIC verifier on the
	// RPC header. Args are not wrapped.
	AuthKrb5 AuthMethod = "krb5"
	// Krb5Integrity (krb5i) — args wrapped with GSS MIC token. Not yet
	// implemented in this build; surfaces as a config-step error.
	AuthKrb5Integrity AuthMethod = "krb5i"
	// Krb5Privacy (krb5p) — args wrapped with GSS Wrap token (encrypted).
	// Not yet implemented in this build; surfaces as a config-step error.
	AuthKrb5Privacy AuthMethod = "krb5p"
)

// ProbeOptions is the input to Probe(). Constructed from CLI flags
// in scanner/cmd/akashic-scanner/test_connection.go.
type ProbeOptions struct {
	Host       string
	Port       uint32 // NFS port; default 2049
	ExportPath string

	AuthMethod  AuthMethod
	AuthUID     uint32 // default 0 (root); Phase 3b makes this configurable per-source
	AuthGID     uint32
	AuthAuxGIDs []uint32

	// Kerberos / RPCSEC_GSS fields. Only consulted when AuthMethod is
	// one of krb5/krb5i/krb5p.
	Krb5Principal        string // user-side principal (e.g., "akashic-svc")
	Krb5Realm            string // realm in canonical UPPERCASE form
	Krb5ServicePrincipal string // SPN; defaults to "nfs/<host>"
	Krb5KeytabPath       string // path to keytab; mutually exclusive with password
	Krb5Password         string // password (passed via stdin); mutually exclusive with keytab
	Krb5ConfigPath       string // alternate krb5.conf; default /etc/krb5.conf

	Timeout time.Duration // per-RPC timeout; total can be ~3× this
}

// Tier identifies which protocol path proved (or failed to prove)
// the export's validity. Surfaced to the user as a confidence
// indicator: mount3/nfsv4 are strong; tcp is "server is up but we
// couldn't validate the export".
type Tier string

const (
	TierMount3 Tier = "mount3"
	TierNFSv4  Tier = "nfsv4"
	TierTCP    Tier = "tcp"
)

// Result is what Probe returns on success. On failure, an error and
// a typed *ProbeError describes which step failed and why.
type Result struct {
	OK         bool
	Tier       Tier
	AuthMethod AuthMethod
	// Warning is non-empty when we fell back to TCP — it tells the
	// user the export wasn't validated, only the server's reachability.
	Warning string
}

// Step categorizes a probe failure so the API can map to its existing
// "step:reason" wire format used by the source-test endpoint.
type Step string

const (
	StepConfig  Step = "config"
	StepConnect Step = "connect"
	StepAuth    Step = "auth"
	StepMount   Step = "mount"
	StepList    Step = "list"
)

// ProbeError carries a typed step + message back to the CLI. The CLI
// formats it as `step:msg` to stderr.
type ProbeError struct {
	Step Step
	Msg  string
}

func (e *ProbeError) Error() string { return string(e.Step) + ":" + e.Msg }

// Probe runs the cascade. Returns ok=true on the first tier that
// successfully validates the export path, or a *ProbeError describing
// the most informative failure observed.
//
// Cascade order:
//   1. MOUNT3 EXPORT — if we can't even list, fall through to NFSv4.
//   2. MOUNT3 MNT/UMNT — actually mount with the chosen auth flavor.
//   3. NFSv4 LOOKUP — for v4-only servers (no portmap/mountd).
//   4. Bare TCP probe — last resort, returns ok with a warning.
//
// The cascade is biased toward "ok" with the strongest evidence we
// could gather: MOUNT3 succeeds → mount3 tier; otherwise NFSv4 succeeds
// → nfsv4 tier; otherwise TCP succeeds → tcp tier with a warning;
// otherwise return the last hard failure.
func Probe(ctx context.Context, opts ProbeOptions) (*Result, error) {
	if opts.Host == "" {
		return nil, &ProbeError{Step: StepConfig, Msg: "host required"}
	}
	if opts.ExportPath == "" {
		return nil, &ProbeError{Step: StepConfig, Msg: "export_path required"}
	}
	if opts.Port == 0 {
		opts.Port = portNFS
	}
	if opts.Timeout == 0 {
		opts.Timeout = 5 * time.Second
	}
	if opts.AuthMethod == "" {
		opts.AuthMethod = AuthSys
	}

	// Kerberos paths take a separate branch — they need a context
	// established before any DATA call, only run against the NFS
	// daemon (not mountd), and currently support only krb5 (not krb5i
	// or krb5p, which would also wrap the args).
	if isKerberos(opts.AuthMethod) {
		return probeKrb5(ctx, opts)
	}

	auth := buildAuth(opts)

	// 1+2. MOUNT3 path: portmap → EXPORT → MNT → UMNT.
	r, definitive := tryMount3(ctx, opts, auth)
	if r != nil {
		return r, nil
	}
	if definitive != nil {
		// MOUNT3 spoke and gave us an authoritative answer (export not
		// in list, or MNT denied). Don't try NFSv4 — it'd just confuse
		// the user with two different reasons for the same underlying
		// fact.
		return nil, definitive
	}

	// 3. NFSv4 path. Reuse the same AUTH_SYS credential.
	if fh, err := nfs4LookupPath(ctx, opts.Host, opts.Port, auth, opts.ExportPath, opts.Timeout); err == nil && len(fh) > 0 {
		return &Result{OK: true, Tier: TierNFSv4, AuthMethod: opts.AuthMethod}, nil
	} else if err != nil {
		// If NFSv4 explicitly rejected the path, surface that as the
		// definitive failure rather than falling through to TCP. NOTDIR
		// and INVAL are just as authoritative as NOENT — they all mean
		// "the path you gave is wrong" — so they short-circuit.
		var e4 *nfs4Error
		if errors.As(err, &e4) {
			switch e4.code {
			case nfs4ErrNoEnt:
				return nil, &ProbeError{
					Step: StepList,
					Msg:  fmt.Sprintf("export path not found on server (%s)", e4.Error()),
				}
			case nfs4ErrNotDir, nfs4ErrInval, nfs4ErrBadHandle:
				return nil, &ProbeError{
					Step: StepList,
					Msg:  fmt.Sprintf("export path invalid (%s)", e4.Error()),
				}
			case nfs4ErrAccess, nfs4ErrPerm:
				return nil, &ProbeError{
					Step: StepAuth,
					Msg:  fmt.Sprintf("client denied by server access rules (%s)", e4.Error()),
				}
			}
		}
		// Unrecognized NFSv4 failure — fall through to TCP.
	}

	// 4. Bare TCP probe.
	if err := tcpReachable(ctx, opts.Host, opts.Port, opts.Timeout); err != nil {
		return nil, &ProbeError{Step: StepConnect, Msg: tidyDialErr(err)}
	}
	return &Result{
		OK:         true,
		Tier:       TierTCP,
		AuthMethod: opts.AuthMethod,
		Warning:    "couldn't validate export path; only confirmed server is listening on the NFS port",
	}, nil
}

func buildAuth(opts ProbeOptions) authBuilder {
	switch opts.AuthMethod {
	case AuthSys:
		return newAuthSys("akashic-probe", opts.AuthUID, opts.AuthGID, opts.AuthAuxGIDs)
	default:
		// Unknown method falls back to AUTH_SYS — the cascade still
		// works, just not with the requested credential. Kerberos
		// methods are intercepted before this call (probeKrb5).
		return newAuthSys("akashic-probe", opts.AuthUID, opts.AuthGID, opts.AuthAuxGIDs)
	}
}

func isKerberos(m AuthMethod) bool {
	return m == AuthKrb5 || m == AuthKrb5Integrity || m == AuthKrb5Privacy
}

// probeKrb5 handles AuthMethod=krb5*. Sequence:
//   1. Reject krb5i / krb5p (deferred to a future phase — args need
//      MIC/Wrap framing).
//   2. Build a gokrb5 client (TGT acquired via keytab or password).
//   3. Establish a GSS context against the NFS daemon (NFSv4 NULLPROC).
//   4. Run NFSv4 LOOKUP using the GSS-protected RPC.
//
// MOUNT3-over-krb5 is not exercised: modern Linux NFSv4 stacks don't
// run mountd over GSS, and the v4 path is the authoritative one for
// any export configured for sec=krb5.
func probeKrb5(ctx context.Context, opts ProbeOptions) (*Result, error) {
	if opts.AuthMethod == AuthKrb5Integrity || opts.AuthMethod == AuthKrb5Privacy {
		return nil, &ProbeError{
			Step: StepConfig,
			Msg:  fmt.Sprintf("auth_method=%s not supported in this build (only sec=krb5 auth-only)", opts.AuthMethod),
		}
	}
	if opts.Krb5Principal == "" || opts.Krb5Realm == "" {
		return nil, &ProbeError{Step: StepConfig, Msg: "krb5 requires principal and realm"}
	}
	if opts.Krb5KeytabPath == "" && opts.Krb5Password == "" {
		return nil, &ProbeError{Step: StepConfig, Msg: "krb5 requires keytab_path or password"}
	}

	kc, err := newKrb5Client(opts.Host, krb5Options{
		Principal:        opts.Krb5Principal,
		Realm:            opts.Krb5Realm,
		ServicePrincipal: opts.Krb5ServicePrincipal,
		KeytabPath:       opts.Krb5KeytabPath,
		Password:         opts.Krb5Password,
		ConfigPath:       opts.Krb5ConfigPath,
	})
	if err != nil {
		return nil, &ProbeError{Step: StepAuth, Msg: err.Error()}
	}

	addr := fmt.Sprintf("%s:%d", opts.Host, opts.Port)
	gssAuth, err := establishGSSContext(ctx, addr, kc, opts.Timeout)
	if err != nil {
		// Establishment failures land in StepAuth — they're auth-side
		// regardless of the underlying cause (KDC unreachable, ticket
		// expired, server doesn't support krb5, etc.).
		return nil, &ProbeError{Step: StepAuth, Msg: err.Error()}
	}

	fh, err := nfs4LookupPath(ctx, opts.Host, opts.Port, gssAuth, opts.ExportPath, opts.Timeout)
	if err != nil {
		var e4 *nfs4Error
		if errors.As(err, &e4) {
			switch e4.code {
			case nfs4ErrNoEnt:
				return nil, &ProbeError{Step: StepList, Msg: fmt.Sprintf("export path not found on server (%s)", e4.Error())}
			case nfs4ErrNotDir, nfs4ErrInval, nfs4ErrBadHandle:
				return nil, &ProbeError{Step: StepList, Msg: fmt.Sprintf("export path invalid (%s)", e4.Error())}
			case nfs4ErrAccess, nfs4ErrPerm:
				return nil, &ProbeError{Step: StepAuth, Msg: fmt.Sprintf("client denied by server access rules (%s)", e4.Error())}
			}
		}
		// RPC-transport failures (TCP refused/timeout, NFS service not
		// running) shouldn't read as auth errors — the krb5 context is
		// already established at this point. Connect-class failures map
		// to StepConnect; auth/reject failures from RPCSEC_GSS itself
		// map to StepAuth.
		var rerr *rpcError
		if errors.As(err, &rerr) {
			switch rerr.kind {
			case rpcErrConnect:
				return nil, &ProbeError{Step: StepConnect, Msg: rerr.msg}
			case rpcErrAccept:
				if rerr.code == rpcProgUnavail {
					return nil, &ProbeError{Step: StepConnect, Msg: "NFSv4 not available on server"}
				}
			}
		}
		return nil, &ProbeError{Step: StepAuth, Msg: err.Error()}
	}
	if len(fh) == 0 {
		return nil, &ProbeError{Step: StepList, Msg: "NFSv4 LOOKUP succeeded but returned an empty filehandle"}
	}
	return &Result{OK: true, Tier: TierNFSv4, AuthMethod: opts.AuthMethod}, nil
}

// tryMount3 attempts the MOUNT3 path. Three return shapes:
//
//   - (*Result, nil)        — full success, return immediately
//   - (nil, *ProbeError)    — MOUNT3 had an authoritative answer
//                             (export not in list, MNT denied) —
//                             abort the cascade
//   - (nil, nil)            — MOUNT3 unreachable / unrecognized error;
//                             fall through to the NFSv4 tier
func tryMount3(ctx context.Context, opts ProbeOptions, auth authBuilder) (*Result, *ProbeError) {
	mountdPort, err := portmapGetPort(ctx, opts.Host, progMount3, versMount3, protoTCP, opts.Timeout)
	if err != nil || mountdPort == 0 {
		return nil, nil
	}

	// EXPORT lists what the server claims to expose. Require an exact
	// path match — `/srv/data` and `/srv/data/foo` are independent
	// exports and matching loosely could cause a false positive.
	exports, expErr := mount3Export(ctx, opts.Host, mountdPort, opts.Timeout)
	if expErr == nil {
		found := false
		for _, e := range exports {
			if e.Path == opts.ExportPath {
				found = true
				break
			}
		}
		if !found {
			// Caveat for the user: servers that restrict EXPORT replies
			// by client-IP/netgroup will hide entries we ARE actually
			// allowed to mount. The MNT call attempted next is more
			// authoritative; if MNT succeeds we never see this error.
			// Surface it only when MNT itself returns NOENT/ACCES,
			// where the user is going to need to check both the path
			// and the export's allowed-clients.
			return nil, &ProbeError{
				Step: StepList,
				Msg: fmt.Sprintf(
					"export %q not visible in server's export list (server reports %d entries; "+
						"some servers restrict EXPORT replies by client IP — verify the export's "+
						"allowed-clients includes this host)",
					opts.ExportPath, len(exports)),
			}
		}
	}
	// EXPORT failures fall through to MNT — some servers reject EXPORT
	// (e.g., AUTH_NONE not configured) while still permitting MNT.

	if _, err := mount3Mnt(ctx, opts.Host, mountdPort, auth, opts.ExportPath, opts.Timeout); err != nil {
		var me *mountError
		if errors.As(err, &me) {
			switch me.code {
			case mnt3ErrAccess, mnt3ErrPerm:
				return nil, &ProbeError{
					Step: StepAuth,
					Msg:  fmt.Sprintf("client denied by server export rules (%s)", me.msg),
				}
			case mnt3ErrNoEnt:
				return nil, &ProbeError{
					Step: StepList,
					Msg:  fmt.Sprintf("export path not found at MNT (%s)", me.msg),
				}
			case mnt3ErrNotDir, mnt3ErrInval:
				return nil, &ProbeError{
					Step: StepList,
					Msg:  fmt.Sprintf("export path invalid (%s)", me.msg),
				}
			}
			// Other MOUNT3 errors fall through to NFSv4 in case the v4
			// path can succeed where v3's MNT couldn't.
			return nil, nil
		}
		// RPC-layer error (timeout, etc.) — fall through.
		return nil, nil
	}

	// Best-effort UMNT cleanup; we don't care if it fails.
	_ = mount3Umnt(ctx, opts.Host, mountdPort, auth, opts.ExportPath, opts.Timeout)
	return &Result{OK: true, Tier: TierMount3, AuthMethod: opts.AuthMethod}, nil
}

func tcpReachable(ctx context.Context, host string, port uint32, timeout time.Duration) error {
	d := net.Dialer{Timeout: timeout}
	addr := fmt.Sprintf("%s:%d", host, port)
	conn, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return err
	}
	_ = conn.Close()
	return nil
}

// tidyDialErr strips the verbose `dial tcp host:port:` prefix and
// duplicate `connect:` tokens that Go adds. The probe's caller
// already knows which host/port was tried.
func tidyDialErr(err error) string {
	s := err.Error()
	if i := strings.LastIndex(s, ": "); i > 0 && strings.HasPrefix(s, "dial tcp") {
		s = s[i+2:]
	}
	return s
}
