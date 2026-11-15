package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewTagCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "tag",
		Short: "Manage tags",
	}

	var addFileID, addTagName string
	addCmd := &cobra.Command{
		Use:   "add",
		Short: "Tag a file",
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			// The server creates the catalogue tag on demand when applying by name.
			fileID, err := client.ParseID("file id", addFileID)
			if err != nil {
				return &ExitErr{Code: ExitUserErr, Err: err}
			}
			tag, err := c.TagFile(ctx, fileID, addTagName)
			if err != nil {
				return fmt.Errorf("tag file: %w", err)
			}
			fmt.Fprintf(cmd.OutOrStdout(), "Tagged file %s with tag %q (id: %s)\n", addFileID, tag.Name, tag.Id)
			return nil
		},
	}
	addCmd.Flags().StringVar(&addFileID, "file", "", "File ID to tag (required)")
	addCmd.Flags().StringVar(&addTagName, "tag", "", "Tag name (required)")
	_ = addCmd.MarkFlagRequired("file")
	_ = addCmd.MarkFlagRequired("tag")

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List all tags",
		RunE: func(cmd *cobra.Command, args []string) error {
			tags, err := c.ListTags(context.Background())
			if err != nil {
				return err
			}
			fmt.Printf("%-36s  %-20s  %s\n", "ID", "NAME", "COLOR")
			for _, t := range tags {
				color := ""
				if t.Color != nil {
					color = *t.Color
				}
				fmt.Printf("%-36s  %-20s  %s\n", t.Id, t.Name, color)
			}
			return nil
		},
	}

	cmd.AddCommand(addCmd, listCmd)
	return cmd
}
