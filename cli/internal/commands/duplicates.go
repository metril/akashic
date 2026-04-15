package commands

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewDuplicatesCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "duplicates",
		Short: "Manage duplicate files",
	}

	var listMinSize int64
	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List duplicate file groups",
		RunE: func(cmd *cobra.Command, args []string) error {
			groups, err := c.ListDuplicates(context.Background(), listMinSize)
			if err != nil {
				return err
			}
			fmt.Printf("%-64s  %5s  %12s  %12s\n", "CONTENT HASH", "COUNT", "TOTAL SIZE", "WASTED")
			for _, g := range groups {
				fmt.Printf("%-64s  %5d  %12d  %12d\n", g.ContentHash, g.Count, g.TotalSize, g.WastedBytes)
			}
			return nil
		},
	}
	listCmd.Flags().Int64Var(&listMinSize, "min-size", 0, "Minimum file size in bytes")

	var reportFormat string
	reportCmd := &cobra.Command{
		Use:   "report",
		Short: "Output duplicate report",
		RunE: func(cmd *cobra.Command, args []string) error {
			groups, err := c.ListDuplicates(context.Background(), 0)
			if err != nil {
				return err
			}
			if reportFormat == "json" {
				enc := json.NewEncoder(os.Stdout)
				enc.SetIndent("", "  ")
				return enc.Encode(groups)
			}
			return fmt.Errorf("unsupported format: %s (use --format json)", reportFormat)
		},
	}
	reportCmd.Flags().StringVar(&reportFormat, "format", "json", "Output format (json)")

	cmd.AddCommand(listCmd, reportCmd)
	return cmd
}
