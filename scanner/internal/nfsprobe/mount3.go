package nfsprobe

import (
	"context"
	"fmt"
	"time"
)

// MOUNT3 protocol (RFC 1813 Appendix I). Two procedures matter for
// the probe:
//
//   - EXPORT (proc 5): returns the export list, no auth required.
//   - MNT (proc 1): authenticates as the chosen flavor, attempts to
//     mount a path, returns the root filehandle on success or an
//     mountstat3 error code.
//   - UMNT (proc 3): cleans up the mount-table entry from MNT. Polite
//     to call after MNT — most servers don't track per-client state
//     beyond what's needed for AUTH_SYS root-squash decisions, but a
//     handful (NetApp, Solaris) keep mountlist entries that grow if
//     UMNT isn't called.

const (
	progMount3 = 100005
	versMount3 = 3

	procMount3Mnt    uint32 = 1
	procMount3Umnt   uint32 = 3
	procMount3Export uint32 = 5
)

// MOUNT3 status codes (mountstat3).
const (
	mnt3Ok           uint32 = 0
	mnt3ErrPerm      uint32 = 1
	mnt3ErrNoEnt     uint32 = 2
	mnt3ErrIO        uint32 = 5
	mnt3ErrAccess    uint32 = 13
	mnt3ErrNotDir    uint32 = 20
	mnt3ErrInval     uint32 = 22
	mnt3ErrNameTooLong uint32 = 63
	mnt3ErrNotSupp   uint32 = 10004
	mnt3ErrServerFault uint32 = 10006
)

func mountStatusName(s uint32) string {
	switch s {
	case mnt3Ok:
		return "OK"
	case mnt3ErrPerm:
		return "MNT3ERR_PERM"
	case mnt3ErrNoEnt:
		return "MNT3ERR_NOENT"
	case mnt3ErrIO:
		return "MNT3ERR_IO"
	case mnt3ErrAccess:
		return "MNT3ERR_ACCES"
	case mnt3ErrNotDir:
		return "MNT3ERR_NOTDIR"
	case mnt3ErrInval:
		return "MNT3ERR_INVAL"
	case mnt3ErrNameTooLong:
		return "MNT3ERR_NAMETOOLONG"
	case mnt3ErrNotSupp:
		return "MNT3ERR_NOTSUPP"
	case mnt3ErrServerFault:
		return "MNT3ERR_SERVERFAULT"
	default:
		return fmt.Sprintf("mountstat3=%d", s)
	}
}

// MountExportEntry is one row from the EXPORT reply. Auth flavors are
// the list the server says it supports for that export — informational;
// the actual client auth is decided independently.
type MountExportEntry struct {
	Path    string
	Groups  []string // allowed-clients list; "*" or empty means everyone
}

// mount3Export issues PMAPPROC_GETPORT then MOUNT3PROC_EXPORT. Returns
// the full export list. AUTH_NONE is used.
func mount3Export(
	ctx context.Context,
	host string,
	mountdPort uint32,
	timeout time.Duration,
) ([]MountExportEntry, error) {
	addr := fmt.Sprintf("%s:%d", host, mountdPort)
	body, err := rpcCallTCP(
		ctx, addr,
		progMount3, versMount3, procMount3Export,
		noAuth, nil, timeout,
	)
	if err != nil {
		return nil, err
	}
	return parseExportReply(body)
}

// Hard caps on the EXPORT reply shape. The 16 MB wire-cap in
// readReplyFragments stops bytes-on-the-wire abuse, but a server can
// still pack ~2 million 1-byte entries into 16 MB and balloon the Go
// heap on `[]MountExportEntry` allocation. These ceilings are well
// above what any real server publishes.
const (
	maxExportEntries     = 1024
	maxGroupsPerExport   = 256
)

// parseExportReply decodes the linked-list-of-(path, groups) the
// server returns. The wire shape (from mount.x in RFC 1813 Appendix I):
//
//   struct exportnode {
//     name      ex_dir;
//     groups    ex_groups;
//     exportnode *ex_next;
//   };
//   typedef exportnode *exports;
//
// XDR linked lists are encoded as [present_bool, value, ...] terminated
// by a 0 bool. The groups list is the same shape: list of name strings
// each prefixed by present_bool.
func parseExportReply(body []byte) ([]MountExportEntry, error) {
	r := newXDRReader(body)
	var out []MountExportEntry
	for {
		more, err := r.readBool()
		if err != nil {
			return nil, fmt.Errorf("export list: %w", err)
		}
		if !more {
			return out, nil
		}
		if len(out) >= maxExportEntries {
			return nil, fmt.Errorf("export list: server returned >%d entries (refusing)", maxExportEntries)
		}
		path, err := r.readString()
		if err != nil {
			return nil, fmt.Errorf("export path: %w", err)
		}
		var groups []string
		for {
			gMore, err := r.readBool()
			if err != nil {
				return nil, fmt.Errorf("export groups: %w", err)
			}
			if !gMore {
				break
			}
			if len(groups) >= maxGroupsPerExport {
				return nil, fmt.Errorf("export groups: >%d per entry (refusing)", maxGroupsPerExport)
			}
			g, err := r.readString()
			if err != nil {
				return nil, fmt.Errorf("group name: %w", err)
			}
			groups = append(groups, g)
		}
		out = append(out, MountExportEntry{Path: path, Groups: groups})
	}
}

// mount3Mnt calls MOUNT3PROC_MNT with the chosen auth flavor. On
// success returns the root filehandle (we don't actually use it for
// anything past the probe; presence proves the mount succeeded).
//
// On `mountstat3 != OK`, returns a typed `*mountError` so the cascade
// can map MNT3ERR_ACCES → "auth", MNT3ERR_NOENT → "list", etc.
func mount3Mnt(
	ctx context.Context,
	host string,
	mountdPort uint32,
	auth authBuilder,
	exportPath string,
	timeout time.Duration,
) ([]byte, error) {
	w := newXDRWriter()
	w.writeString(exportPath)
	addr := fmt.Sprintf("%s:%d", host, mountdPort)
	body, err := rpcCallTCP(
		ctx, addr,
		progMount3, versMount3, procMount3Mnt,
		auth, w.bytes(), timeout,
	)
	if err != nil {
		return nil, err
	}
	r := newXDRReader(body)
	stat, err := r.readUint32()
	if err != nil {
		return nil, fmt.Errorf("mountstat3: %w", err)
	}
	if stat != mnt3Ok {
		return nil, &mountError{code: stat, msg: mountStatusName(stat)}
	}
	// On OK, the reply continues with the filehandle then the list of
	// allowed auth flavors. We only need the filehandle.
	fh, err := r.readOpaque()
	if err != nil {
		return nil, fmt.Errorf("filehandle: %w", err)
	}
	return fh, nil
}

// mount3Umnt is best-effort cleanup. Failure is logged at the call
// site and otherwise ignored.
func mount3Umnt(
	ctx context.Context,
	host string,
	mountdPort uint32,
	auth authBuilder,
	exportPath string,
	timeout time.Duration,
) error {
	w := newXDRWriter()
	w.writeString(exportPath)
	addr := fmt.Sprintf("%s:%d", host, mountdPort)
	_, err := rpcCallTCP(
		ctx, addr,
		progMount3, versMount3, procMount3Umnt,
		auth, w.bytes(), timeout,
	)
	return err
}

// mountError is the typed shape for non-OK MOUNT3 statuses.
type mountError struct {
	code uint32
	msg  string
}

func (e *mountError) Error() string { return e.msg }
