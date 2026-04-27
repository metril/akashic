package lsarpc

import (
	"errors"
	"fmt"
	"io"
)

type Transport interface {
	io.ReadWriteCloser
}

type Client struct {
	t      Transport
	callID uint32
	handle PolicyHandle
	bound  bool
	opened bool
}

func NewClient(t Transport) *Client {
	return &Client{t: t, callID: 1}
}

func (c *Client) Bind() error {
	pkt := BuildBindRequest(c.nextCall(), 4280, 4280)
	if _, err := c.t.Write(pkt); err != nil {
		return err
	}
	resp, err := c.readPDU()
	if err != nil {
		return err
	}
	hdr, _ := ParsePDUHeader(resp)
	if hdr.PType != PtypeBindAck {
		return fmt.Errorf("%w: got ptype %d", ErrBindFailed, hdr.PType)
	}
	c.bound = true
	return nil
}

func (c *Client) Open() error {
	if !c.bound {
		return errors.New("lsarpc: not bound")
	}
	pkt := BuildOpenPolicy2Request(c.nextCall(), 0x00000800)
	if _, err := c.t.Write(pkt); err != nil {
		return err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return err
	}
	h, status, err := ParseOpenPolicy2Response(body)
	if err != nil {
		return err
	}
	if status != 0 {
		return fmt.Errorf("LsarOpenPolicy2 ntstatus=0x%x", status)
	}
	c.handle = h
	c.opened = true
	return nil
}

func (c *Client) Lookup(sids [][]byte) ([]TranslatedName, error) {
	if !c.opened {
		return nil, errors.New("lsarpc: policy not open")
	}
	pkt, err := BuildLookupSids2Request(c.nextCall(), c.handle, sids)
	if err != nil {
		return nil, err
	}
	if _, err := c.t.Write(pkt); err != nil {
		return nil, err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return nil, err
	}
	names, _, err := ParseLookupSids2Response(body)
	return names, err
}

func (c *Client) Close() error {
	// Best-effort LsarClose to release the server-side policy handle. Errors
	// here are non-fatal — the transport teardown that follows will clean up
	// regardless.
	if c.opened && c.t != nil {
		pkt := BuildLsarCloseRequest(c.nextCall(), c.handle)
		if _, werr := c.t.Write(pkt); werr == nil {
			_, _ = c.readResponseBody()
		}
		c.opened = false
	}
	if c.t != nil {
		_ = c.t.Close()
	}
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
	hdr, err := ParsePDUHeader(hdrBuf)
	if err != nil {
		return nil, err
	}
	if hdr.FragLen < 16 {
		return nil, ErrTruncated
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
		return nil, ErrTruncated
	}
	return pdu[24:], nil
}
