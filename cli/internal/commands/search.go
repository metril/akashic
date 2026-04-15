package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewSearchCmd(c *client.Client) *cobra.Command {
	var sourceID, extension string

	cmd := &cobra.Command{
		Use:   "search [query]",
		Short: "Search indexed files",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			params := &client.SearchParams{SourceID: sourceID, Extension: extension}
			results, err := c.Search(context.Background(), args[0], params)
			if err != nil {
				return err
			}
			fmt.Printf("Found %d results for '%s'\n\n", results.Total, results.Query)
			for _, f := range results.Results {
				fmt.Printf("  %s  (%d bytes)  %s\n", f.Path, f.SizeBytes, f.SourceID)
			}
			return nil
		},
	}

	cmd.Flags().StringVar(&sourceID, "source", "", "Filter by source ID")
	cmd.Flags().StringVar(&extension, "type", "", "Filter by file extension")
	return cmd
}
