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

	var addName, addType, addHost, addPath string
	addCmd := &cobra.Command{
		Use:   "add",
		Short: "Add a new source",
		RunE: func(cmd *cobra.Command, args []string) error {
			config := map[string]string{}
			if addHost != "" {
				config["host"] = addHost
			}
			if addPath != "" {
				config["path"] = addPath
			}
			source, err := c.CreateSource(context.Background(), addName, addType, config)
			if err != nil {
				return err
			}
			fmt.Printf("Created source %s (id: %s)\n", source.Name, source.ID)
			return nil
		},
	}
	addCmd.Flags().StringVar(&addName, "name", "", "Source name (required)")
	addCmd.Flags().StringVar(&addType, "type", "", "Source type (required)")
	addCmd.Flags().StringVar(&addHost, "host", "", "Host address")
	addCmd.Flags().StringVar(&addPath, "path", "", "Path on host")
	_ = addCmd.MarkFlagRequired("name")
	_ = addCmd.MarkFlagRequired("type")

	statusCmd := &cobra.Command{
		Use:   "status",
		Short: "Show status for all sources",
		RunE: func(cmd *cobra.Command, args []string) error {
			sources, err := c.ListSources(context.Background())
			if err != nil {
				return err
			}
			fmt.Printf("%-36s  %-20s  %-8s  %-10s  %s\n", "ID", "NAME", "TYPE", "STATUS", "LAST SCAN")
			for _, s := range sources {
				lastScan := s.LastScanAt
				if lastScan == "" {
					lastScan = "never"
				}
				fmt.Printf("%-36s  %-20s  %-8s  %-10s  %s\n", s.ID, s.Name, s.Type, s.Status, lastScan)
			}
			return nil
		},
	}

	cmd.AddCommand(listCmd, addCmd, statusCmd)
	return cmd
}
