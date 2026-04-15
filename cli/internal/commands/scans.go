package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

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
				return err
			}
			fmt.Printf("Scan triggered for source: %s\n", triggerSource)
			return nil
		},
	}
	triggerCmd.Flags().StringVar(&triggerSource, "source", "", "Source name to scan (required)")
	_ = triggerCmd.MarkFlagRequired("source")

	statusCmd := &cobra.Command{
		Use:   "status",
		Short: "Show recent scan status",
		RunE: func(cmd *cobra.Command, args []string) error {
			scans, err := c.ListScans(context.Background(), 5)
			if err != nil {
				return err
			}
			fmt.Printf("%-36s  %-36s  %-10s  %-12s  %s\n", "ID", "SOURCE ID", "STATUS", "FILES FOUND", "STARTED AT")
			for _, s := range scans {
				fmt.Printf("%-36s  %-36s  %-10s  %-12d  %s\n", s.ID, s.SourceID, s.Status, s.FilesFound, s.StartedAt)
			}
			return nil
		},
	}

	cmd.AddCommand(triggerCmd, statusCmd)
	return cmd
}
