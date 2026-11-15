package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/apiclient"
	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewSearchCmd(c *client.Client) *cobra.Command {
	var sourceID, extension string

	cmd := &cobra.Command{
		Use:   "search [query]",
		Short: "Search indexed files",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var params apiclient.SearchParams
			if sourceID != "" {
				id, err := client.ParseID("source id", sourceID)
				if err != nil {
					return &ExitErr{Code: ExitUserErr, Err: err}
				}
				params.SourceId = &id
			}
			if extension != "" {
				params.Extension = &extension
			}
			results, err := c.Search(context.Background(), args[0], params)
			if err != nil {
				return err
			}
			total := len(results.Items)
			if results.Total != nil {
				total = *results.Total
			}
			fmt.Printf("Found %d results for '%s'\n\n", total, results.Query)
			for _, f := range results.Items {
				var size int
				if f.SizeBytes != nil {
					size = *f.SizeBytes
				}
				source := ""
				if f.SourceId != nil {
					source = f.SourceId.String()
				}
				fmt.Printf("  %s  (%d bytes)  %s\n", f.Path, size, source)
			}
			return nil
		},
	}

	cmd.Flags().StringVar(&sourceID, "source", "", "Filter by source ID")
	cmd.Flags().StringVar(&extension, "type", "", "Filter by file extension")
	return cmd
}
