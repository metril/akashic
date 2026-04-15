package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewPurgeCmd(c *client.Client) *cobra.Command {
	var sourceID string
	var confirm bool

	cmd := &cobra.Command{
		Use:   "purge",
		Short: "Purge files from a source",
		RunE: func(cmd *cobra.Command, args []string) error {
			if !confirm {
				return fmt.Errorf("pass --confirm to confirm purge of source %s", sourceID)
			}
			if err := c.PurgeSource(context.Background(), sourceID); err != nil {
				return err
			}
			fmt.Printf("Purged source %s\n", sourceID)
			return nil
		},
	}
	cmd.Flags().StringVar(&sourceID, "source", "", "Source ID to purge (required)")
	cmd.Flags().BoolVar(&confirm, "confirm", false, "Confirm purge operation")
	_ = cmd.MarkFlagRequired("source")

	return cmd
}
