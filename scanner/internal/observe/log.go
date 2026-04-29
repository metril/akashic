package observe

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"
)

// logDrainBatchSize / logDrainInterval — flush whenever EITHER fires.
// 10 lines amortizes the request cost without making the UI feel laggy;
// 500 ms is the upper bound on "I just wrote something but it hasn't
// shown up" perception.
const (
	logBufferCap     = 1000
	logDrainBatch    = 10
	logDrainInterval = 500 * time.Millisecond
)

// LogLine is the wire shape of one structured log entry.
type LogLine struct {
	Timestamp time.Time `json:"ts"`
	Level     string    `json:"level"` // "info" | "warn" | "error"
	Message   string    `json:"message"`
}

// LogSink is the writer side of the log channel. The drain goroutine
// reads from `ch` and POSTs in batches.
type LogSink struct {
	ch     chan LogLine
	once   sync.Once
	closed chan struct{}

	// dropped is incremented whenever the channel is full. Surfaced as a
	// single warn line on the next successful flush, so the user sees
	// "log overflow: 47 lines dropped" rather than silent gaps.
	mu      sync.Mutex
	dropped int64
}

func newLogSink() *LogSink {
	return &LogSink{
		ch:     make(chan LogLine, logBufferCap),
		closed: make(chan struct{}),
	}
}

func (s *LogSink) emit(level, format string, args ...any) {
	if s == nil {
		return
	}
	line := LogLine{
		Timestamp: time.Now().UTC(),
		Level:     level,
		Message:   fmt.Sprintf(format, args...),
	}
	select {
	case s.ch <- line:
	default:
		// Channel full: drop and bump the dropped counter. We must NOT
		// block — the scanner's hot path emits log lines and any block
		// here would slow indexing.
		s.mu.Lock()
		s.dropped++
		s.mu.Unlock()
	}
}

func (s *LogSink) Info(format string, args ...any)  { s.emit("info", format, args...) }
func (s *LogSink) Warn(format string, args ...any)  { s.emit("warn", format, args...) }
func (s *LogSink) Error(format string, args ...any) { s.emit("error", format, args...) }

func (s *LogSink) close() {
	s.once.Do(func() { close(s.closed) })
}

// takeDropped returns and resets the dropped-line count.
func (s *LogSink) takeDropped() int64 {
	s.mu.Lock()
	d := s.dropped
	s.dropped = 0
	s.mu.Unlock()
	return d
}

// runLogDrain consumes the channel, batches by size or interval, and POSTs.
// The goroutine exits when ctx is cancelled AND the channel has been
// drained — partial buffers get one final flush against a fresh context
// so the cancel doesn't prevent the last batch from going out.
func (r *Reporter) runLogDrain(ctx context.Context) {
	t := time.NewTicker(logDrainInterval)
	defer t.Stop()

	pending := make([]LogLine, 0, logDrainBatch)

	flushWith := func(c context.Context) {
		if dropped := r.logSink.takeDropped(); dropped > 0 {
			pending = append(pending, LogLine{
				Timestamp: time.Now().UTC(),
				Level:     "warn",
				Message:   fmt.Sprintf("log overflow: %d lines dropped (channel saturated)", dropped),
			})
		}
		if len(pending) == 0 {
			return
		}
		r.postLogBatch(c, pending)
		pending = pending[:0]
	}
	flush := func() { flushWith(ctx) }

	finalDrain := func() {
		// Drain the channel and flush against a 5 s background context so
		// the in-flight cancel doesn't kill the request mid-write.
		for {
			select {
			case line := <-r.logSink.ch:
				pending = append(pending, line)
			default:
				finalCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				flushWith(finalCtx)
				cancel()
				return
			}
		}
	}

	for {
		select {
		case <-ctx.Done():
			finalDrain()
			return
		case <-r.logSink.closed:
			finalDrain()
			return
		case line := <-r.logSink.ch:
			pending = append(pending, line)
			if len(pending) >= logDrainBatch {
				flush()
			}
		case <-t.C:
			flush()
		}
	}
}

func (r *Reporter) postLogBatch(ctx context.Context, lines []LogLine) {
	body, err := json.Marshal(map[string]any{"lines": lines})
	if err != nil {
		return
	}
	url := r.apiURL + "/api/scans/" + r.scanID + "/log"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.apiKey)

	resp, err := r.httpClient.Do(req)
	if err != nil {
		return
	}
	resp.Body.Close()
}
