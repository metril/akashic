package nfsprobe

// AUTH_NONE is the trivial auth flavor — no identity asserted. Used by:
//
//   - Portmapper PMAPPROC_GETPORT: convention is AUTH_NONE; servers
//     don't gate the lookup on uid.
//   - MOUNT3 EXPORT: returns the public export list; AUTH_NONE works
//     against every server we've seen.
//
// MOUNT3 MNT and NFSv4 LOOKUP need a real identity (typically AUTH_SYS)
// — see auth_sys.go.

type authNoneBuilder struct{}

func (authNoneBuilder) cred() authBody {
	return authBody{flavor: authNone, body: nil}
}

func (authNoneBuilder) verifier() authBody {
	return authBody{flavor: authNone, body: nil}
}

// noAuth is a singleton — auth_none has no per-call state.
var noAuth = authNoneBuilder{}
