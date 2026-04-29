package samr

import (
	"errors"
	"fmt"
	"io"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// Transport is satisfied by anything supporting bidirectional byte
// communication — typically the SMB IPC$ named-pipe handle to \PIPE\samr.
type Transport interface {
	io.ReadWriteCloser
}

// Client is a SAMR session manager. It tracks the call ID counter and the
// open server/domain/user handles so Close can release them in the right
// order.
type Client struct {
	t            Transport
	callID       uint32
	serverHandle Handle
	domainHandle Handle
	userHandle   Handle

	bound        bool
	hasServer    bool
	hasDomain    bool
	hasUser      bool
}

// NewClient creates a new client over the given transport. The transport
// is closed by Close().
func NewClient(t Transport) *Client {
	return &Client{t: t, callID: 1}
}

// Bind sends the DCE/RPC bind PDU and validates the bind_ack.
func (c *Client) Bind() error {
	pkt := BuildBindRequest(c.nextCall(), 4280, 4280)
	if _, err := c.t.Write(pkt); err != nil {
		return err
	}
	resp, err := c.readPDU()
	if err != nil {
		return err
	}
	hdr, _ := dcerpc.ParsePDUHeader(resp)
	if hdr.PType != dcerpc.PtypeBindAck {
		return fmt.Errorf("%w: got ptype %d", dcerpc.ErrBindFailed, hdr.PType)
	}
	c.bound = true
	return nil
}

// Connect runs SamrConnect5 and stores the server handle.
func (c *Client) Connect(serverName string) error {
	if !c.bound {
		return errors.New("samr: not bound")
	}
	pkt := BuildSamrConnect5Request(c.nextCall(), serverName, SamServerLookupDomain|SamServerConnect)
	if _, err := c.t.Write(pkt); err != nil {
		return err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return err
	}
	h, status, err := ParseSamrConnect5Response(body)
	if err != nil {
		return err
	}
	if status != 0 {
		return &StatusError{Op: "SamrConnect5", Status: status}
	}
	c.serverHandle = h
	c.hasServer = true
	return nil
}

// OpenDomain runs SamrOpenDomain on the connected server.
func (c *Client) OpenDomain(domain SID) error {
	if !c.hasServer {
		return errors.New("samr: server handle not open")
	}
	access := DomainLookup | DomainListAccounts
	pkt := BuildSamrOpenDomainRequest(c.nextCall(), c.serverHandle, access, domain)
	if _, err := c.t.Write(pkt); err != nil {
		return err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return err
	}
	h, status, err := ParseSamrOpenDomainResponse(body)
	if err != nil {
		return err
	}
	if status != 0 {
		return &StatusError{Op: "SamrOpenDomain", Status: status}
	}
	c.domainHandle = h
	c.hasDomain = true
	return nil
}

// OpenUser runs SamrOpenUser on the open domain.
func (c *Client) OpenUser(rid uint32) error {
	if !c.hasDomain {
		return errors.New("samr: domain handle not open")
	}
	pkt := BuildSamrOpenUserRequest(c.nextCall(), c.domainHandle, UserReadGroupInformation, rid)
	if _, err := c.t.Write(pkt); err != nil {
		return err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return err
	}
	h, status, err := ParseSamrOpenUserResponse(body)
	if err != nil {
		return err
	}
	if status != 0 {
		return &StatusError{Op: "SamrOpenUser", Status: status}
	}
	c.userHandle = h
	c.hasUser = true
	return nil
}

// GetGroupsForUser returns the list of group RIDs the open user belongs to.
func (c *Client) GetGroupsForUser() ([]uint32, error) {
	if !c.hasUser {
		return nil, errors.New("samr: user handle not open")
	}
	pkt := BuildSamrGetGroupsForUserRequest(c.nextCall(), c.userHandle)
	if _, err := c.t.Write(pkt); err != nil {
		return nil, err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return nil, err
	}
	groups, status, err := ParseSamrGetGroupsForUserResponse(body)
	if err != nil {
		return nil, err
	}
	if status != 0 {
		return nil, &StatusError{Op: "SamrGetGroupsForUser", Status: status}
	}
	rids := make([]uint32, len(groups))
	for i, g := range groups {
		rids[i] = g.RID
	}
	return rids, nil
}

// LookupIds resolves the given group RIDs to names via SamrLookupIdsInDomain.
func (c *Client) LookupIds(rids []uint32) ([]string, error) {
	if !c.hasDomain {
		return nil, errors.New("samr: domain handle not open")
	}
	if len(rids) == 0 {
		return nil, nil
	}
	pkt := BuildSamrLookupIdsInDomainRequest(c.nextCall(), c.domainHandle, rids)
	if _, err := c.t.Write(pkt); err != nil {
		return nil, err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return nil, err
	}
	names, status, err := ParseSamrLookupIdsInDomainResponse(body)
	if err != nil {
		return nil, err
	}
	if status != 0 {
		return nil, &StatusError{Op: "SamrLookupIdsInDomain", Status: status}
	}
	return names, nil
}

// Close releases any open server/domain/user handles in reverse order
// (best-effort), then closes the underlying transport. Errors during
// handle release are non-fatal — the transport teardown that follows
// cleans up regardless.
func (c *Client) Close() error {
	if c.t == nil {
		return nil
	}
	if c.hasUser {
		_ = c.closeHandleBestEffort(c.userHandle)
		c.hasUser = false
	}
	if c.hasDomain {
		_ = c.closeHandleBestEffort(c.domainHandle)
		c.hasDomain = false
	}
	if c.hasServer {
		_ = c.closeHandleBestEffort(c.serverHandle)
		c.hasServer = false
	}
	_ = c.t.Close()
	return nil
}

func (c *Client) closeHandleBestEffort(h Handle) error {
	pkt := BuildSamrCloseHandleRequest(c.nextCall(), h)
	if _, werr := c.t.Write(pkt); werr != nil {
		return werr
	}
	_, _ = c.readResponseBody()
	return nil
}

func (c *Client) nextCall() uint32 {
	v := c.callID
	c.callID++
	return v
}

func (c *Client) readPDU() ([]byte, error) {
	hdrBuf := make([]byte, 16)
	if _, err := io.ReadFull(c.t, hdrBuf); err != nil {
		return nil, err
	}
	hdr, err := dcerpc.ParsePDUHeader(hdrBuf)
	if err != nil {
		return nil, err
	}
	if hdr.FragLen < 16 {
		return nil, dcerpc.ErrTruncated
	}
	rest := make([]byte, int(hdr.FragLen)-16)
	if _, err := io.ReadFull(c.t, rest); err != nil {
		return nil, err
	}
	return append(hdrBuf, rest...), nil
}

func (c *Client) readResponseBody() ([]byte, error) {
	pdu, err := c.readPDU()
	if err != nil {
		return nil, err
	}
	if len(pdu) < 24 {
		return nil, dcerpc.ErrTruncated
	}
	return pdu[24:], nil
}

// ResolveGroupsForSid runs the full SAMR sequence for one user SID:
//
//	Bind → Connect → OpenDomain(domainSid) → OpenUser(rid) →
//	GetGroupsForUser → LookupIds → Close
//
// Returns the list of group names. server is the SamrConnect5 ServerName
// argument (typically `"\\\\hostname"`). userSid is the full user SID; it's
// split into domain SID + RID internally.
func ResolveGroupsForSid(t Transport, server string, userSid SID) ([]string, error) {
	domain, rid, err := SplitDomainAndRid(userSid)
	if err != nil {
		return nil, err
	}
	c := NewClient(t)
	defer c.Close()
	if err := c.Bind(); err != nil {
		return nil, fmt.Errorf("bind: %w", err)
	}
	if err := c.Connect(server); err != nil {
		return nil, fmt.Errorf("connect: %w", err)
	}
	if err := c.OpenDomain(domain); err != nil {
		return nil, fmt.Errorf("open_domain: %w", err)
	}
	if err := c.OpenUser(rid); err != nil {
		return nil, fmt.Errorf("open_user: %w", err)
	}
	rids, err := c.GetGroupsForUser()
	if err != nil {
		return nil, fmt.Errorf("get_groups: %w", err)
	}
	if len(rids) == 0 {
		return nil, nil
	}
	return c.LookupIds(rids)
}
