package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewSourcesCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "sources",
		Short: "Manage sources",
	}

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List all sources",
		RunE: func(cmd *cobra.Command, args []string) error {
			sources, err := c.ListSources(context.Background())
			if err != nil {
				return err
			}
			fmt.Printf("%-20s  %-8s  %-10s  %s\n", "NAME", "TYPE", "STATUS", "LAST SCAN")
			for _, s := range sources {
				lastScan := s.LastScanAt
				if lastScan == "" {
					lastScan = "never"
				}
				fmt.Printf("%-20s  %-8s  %-10s  %s\n", s.Name, s.Type, s.Status, lastScan)
			}
			return nil
		},
	}

	cmd.AddCommand(listCmd)
	return cmd
}
