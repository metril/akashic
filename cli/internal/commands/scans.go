package commands

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

// Exit codes — surfaced to the caller via the `ExitErr` type so main()
// can translate them. cobra's RunE returns errors; main inspects the
// error chain to pick the right exit code.
const (
	ExitOK         = 0
	ExitUserErr    = 1 // bad args, 4xx
	ExitServerErr  = 2 // 5xx, network failure
	ExitScanFailed = 3 // scan reached terminal `failed` state
)

// ExitErr lets a command request a specific exit code from main().
// Wrap an underlying error so the message still surfaces.
type ExitErr struct {
	Code int
	Err  error
}

func (e *ExitErr) Error() string { return e.Err.Error() }
func (e *ExitErr) Unwrap() error { return e.Err }

// classifyAPIError turns a client.APIError into the appropriate
// ExitErr code, or leaves other errors alone (treated as server errors).
func classifyAPIError(err error) error {
	if err == nil {
		return nil
	}
	var apiErr *client.APIError
	if errors.As(err, &apiErr) {
		switch {
		case apiErr.Status >= 500:
			return &ExitErr{Code: ExitServerErr, Err: err}
		case apiErr.Status >= 400:
			return &ExitErr{Code: ExitUserErr, Err: err}
		}
	}
	return &ExitErr{Code: ExitServerErr, Err: err}
}

// terminalScanStatuses are the states `scan wait` should stop at.
var terminalScanStatuses = map[string]bool{
	"completed": true,
	"failed":    true,
	"cancelled": true,
}

func NewScanCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "scan",
		Short: "Manage scans",
	}

	var triggerSource string
	triggerCmd := &cobra.Command{
		Use:   "trigger",
		Short: "Trigger a scan for a source",
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := c.TriggerScan(context.Background(), triggerSource); err != nil {
				return classifyAPIError(err)
			}
			fmt.Printf("Scan triggered for source: %s\n", triggerSource)
			return nil
		},
	}
	triggerCmd.Flags().StringVar(&triggerSource, "source", "", "Source name to scan (required)")
	_ = triggerCmd.MarkFlagRequired("source")

	statusCmd := &cobra.Command{
		Use:   "status",
		Short: "Show recent scan status (alias of `list --limit 5`)",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runListScans(c, 5)
		},
	}

	var listLimit int
	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List recent scans",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runListScans(c, listLimit)
		},
	}
	listCmd.Flags().IntVar(&listLimit, "limit", 20, "Maximum scans to show")

	cancelCmd := &cobra.Command{
		Use:   "cancel <scan-id>",
		Short: "Cancel a running scan",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := c.CancelScan(context.Background(), args[0]); err != nil {
				return classifyAPIError(err)
			}
			fmt.Printf("Cancelled scan %s\n", args[0])
			return nil
		},
	}

	var waitTimeout time.Duration
	var waitInterval time.Duration
	waitCmd := &cobra.Command{
		Use:   "wait <scan-id>",
		Short: "Block until a scan reaches a terminal state",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, cancel := context.WithTimeout(context.Background(), waitTimeout)
			defer cancel()
			ticker := time.NewTicker(waitInterval)
			defer ticker.Stop()
			for {
				scan, err := c.GetScan(ctx, args[0])
				if err != nil {
					return classifyAPIError(err)
				}
				if terminalScanStatuses[scan.Status] {
					fmt.Printf("scan %s -> %s\n", scan.ID, scan.Status)
					if scan.Status == "failed" {
						return &ExitErr{
							Code: ExitScanFailed,
							Err:  fmt.Errorf("scan %s failed", scan.ID),
						}
					}
					return nil
				}
				select {
				case <-ctx.Done():
					return &ExitErr{
						Code: ExitUserErr,
						Err:  fmt.Errorf("timed out waiting for scan %s", args[0]),
					}
				case <-ticker.C:
				}
			}
		},
	}
	waitCmd.Flags().DurationVar(&waitTimeout, "timeout", 30*time.Minute, "Max wait duration")
	waitCmd.Flags().DurationVar(&waitInterval, "interval", 2*time.Second, "Poll interval")

	cmd.AddCommand(triggerCmd, statusCmd, listCmd, cancelCmd, waitCmd)
	return cmd
}

// runListScans is shared by `status` and `list`.
func runListScans(c *client.Client, limit int) error {
	scans, err := c.ListScans(context.Background(), limit)
	if err != nil {
		return classifyAPIError(err)
	}
	fmt.Printf("%-36s  %-36s  %-10s  %-12s  %s\n", "ID", "SOURCE ID", "STATUS", "FILES FOUND", "STARTED AT")
	for _, s := range scans {
		fmt.Printf("%-36s  %-36s  %-10s  %-12d  %s\n", s.ID, s.SourceID, s.Status, s.FilesFound, s.StartedAt)
	}
	return nil
}
