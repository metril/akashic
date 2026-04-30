package nfsprobe

import "time"

// AUTH_SYS (also known as AUTH_UNIX) presents a uid/gid identity. RFC
// 5531 §8.2 defines the cred body as:
//
//   struct authsys_parms {
//     unsigned int stamp;       /* arbitrary, just needs to differ */
//     string       machinename<255>;
//     unsigned int uid;
//     unsigned int gid;
//     unsigned int gids<16>;    /* aux gids, max 16 */
//   };
//
// The verifier is AUTH_NONE.
//
// Servers map (uid, gid, gids) to access decisions — root squashing
// (uid=0 → nobody), allowed-ranges, etc. The probe defaults to uid=0
// because that's what most exports allow for read-side operations like
// LOOKUP; Phase 3b makes it configurable per-source.

type authSysBuilder struct {
	machineName string
	uid         uint32
	gid         uint32
	auxGids     []uint32
	stamp       uint32
}

// newAuthSys builds an AUTH_SYS credential builder. `machineName` is
// informational; servers don't usually gate on it. Empty string is
// fine (kernel client typically sends the hostname).
//
// `auxGids` is capped at 16 per the RFC; surplus entries are
// truncated rather than rejected.
func newAuthSys(machineName string, uid, gid uint32, auxGids []uint32) *authSysBuilder {
	if len(auxGids) > 16 {
		auxGids = auxGids[:16]
	}
	// stamp differentiates calls within a session; using nano-time keeps
	// it monotonic across the lifetime of the probe.
	stamp := uint32(time.Now().UnixNano())
	return &authSysBuilder{
		machineName: machineName,
		uid:         uid,
		gid:         gid,
		auxGids:     auxGids,
		stamp:       stamp,
	}
}

func (a *authSysBuilder) cred() authBody {
	w := newXDRWriter()
	w.writeUint32(a.stamp)
	w.writeString(a.machineName)
	w.writeUint32(a.uid)
	w.writeUint32(a.gid)
	w.writeUint32List(a.auxGids)
	return authBody{flavor: authSys, body: w.bytes()}
}

func (a *authSysBuilder) verifier() authBody {
	// Verifier is AUTH_NONE for AUTH_SYS calls — servers don't validate
	// it, but the field still has to be present.
	return authBody{flavor: authNone, body: nil}
}
