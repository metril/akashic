package commands

import (
	"context"
	"fmt"
	"text/tabwriter"

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
				fmt.Printf("%-20s  %-8s  %-10s  %s\n", s.Name, s.Type, s.Status, formatTime(s.LastScanAt, "never"))
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
			fmt.Printf("Created source %s (id: %s)\n", source.Name, source.Id)
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
				fmt.Printf("%-36s  %-20s  %-8s  %-10s  %s\n", s.Id, s.Name, s.Type, s.Status, formatTime(s.LastScanAt, "never"))
			}
			return nil
		},
	}

	cmd.AddCommand(listCmd, addCmd, statusCmd, newMirrorCmd(c))
	return cmd
}

// mirrorMappings reads connection_config.mirror_of as generic maps.
func mirrorMappings(cfg map[string]any) []map[string]any {
	raw, _ := cfg["mirror_of"].([]any)
	out := make([]map[string]any, 0, len(raw))
	for _, e := range raw {
		if m, ok := e.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func str(m map[string]any, k string) string {
	s, _ := m[k].(string)
	return s
}

func newMirrorCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "mirror",
		Short: "Link a backing source's files to Immich/Paperless entries",
	}

	var target, remotePrefix, localPrefix string
	setCmd := &cobra.Command{
		Use:   "set <backing>",
		Short: "Map a target's remote path prefix onto the backing source",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			backing, err := c.ResolveSource(ctx, args[0])
			if err != nil {
				return err
			}
			tgt, err := c.ResolveSource(ctx, target)
			if err != nil {
				return err
			}
			full, err := c.GetSource(ctx, backing.Id)
			if err != nil {
				return err
			}
			tid := tgt.Id.String()
			list := []any{}
			for _, m := range mirrorMappings(full.ConnectionConfig) {
				if str(m, "source_id") != tid {
					list = append(list, m)
				}
			}
			list = append(list, map[string]any{"source_id": tid, "remote_prefix": remotePrefix, "local_prefix": localPrefix})
			updated, err := c.UpdateSourceConfig(ctx, backing.Id, map[string]any{"mirror_of": list})
			if err != nil {
				return err
			}
			var rp, lp string
			found := false
			for _, m := range mirrorMappings(updated.ConnectionConfig) {
				if str(m, "source_id") == tid {
					rp, lp, found = str(m, "remote_prefix"), str(m, "local_prefix"), true
				}
			}
			if !found {
				return fmt.Errorf("server response has no mirror mapping for %s", tgt.Name)
			}
			fmt.Fprintf(cmd.OutOrStdout(), "Mirror set: %s → %s (remote %q → local %q)\n", backing.Name, tgt.Name, rp, lp)
			return nil
		},
	}
	setCmd.Flags().StringVar(&target, "target", "", "Immich/Paperless source to link to (name or id, required)")
	setCmd.Flags().StringVar(&remotePrefix, "remote-prefix", "", "Path prefix as the target app sees it (required)")
	setCmd.Flags().StringVar(&localPrefix, "local-prefix", "", "Matching path prefix in the backing source (required)")
	_ = setCmd.MarkFlagRequired("target")
	_ = setCmd.MarkFlagRequired("remote-prefix")
	_ = setCmd.MarkFlagRequired("local-prefix")

	var clearTarget string
	clearCmd := &cobra.Command{
		Use:   "clear <backing>",
		Short: "Remove one target's mapping, or all mappings without --target",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			backing, err := c.ResolveSource(ctx, args[0])
			if err != nil {
				return err
			}
			list := []any{}
			var tgtName string
			if clearTarget != "" {
				tgt, err := c.ResolveSource(ctx, clearTarget)
				if err != nil {
					return err
				}
				full, err := c.GetSource(ctx, backing.Id)
				if err != nil {
					return err
				}
				tgtName = tgt.Name
				removed := false
				for _, m := range mirrorMappings(full.ConnectionConfig) {
					if str(m, "source_id") != tgt.Id.String() {
						list = append(list, m)
					} else {
						removed = true
					}
				}
				if !removed {
					return fmt.Errorf("%s has no mirror mapping for %s", backing.Name, tgt.Name)
				}
			}
			if _, err := c.UpdateSourceConfig(ctx, backing.Id, map[string]any{"mirror_of": list}); err != nil {
				return err
			}
			if clearTarget != "" {
				fmt.Fprintf(cmd.OutOrStdout(), "Mirror cleared: %s → %s\n", backing.Name, tgtName)
			} else {
				fmt.Fprintf(cmd.OutOrStdout(), "Mirror cleared: %s (all targets)\n", backing.Name)
			}
			return nil
		},
	}
	clearCmd.Flags().StringVar(&clearTarget, "target", "", "Only clear this target (name or id)")

	showCmd := &cobra.Command{
		Use:   "show <backing>",
		Short: "Show mirror mappings and the per-role storage breakdown",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			backing, err := c.ResolveSource(ctx, args[0])
			if err != nil {
				return err
			}
			full, err := c.GetSource(ctx, backing.Id)
			if err != nil {
				return err
			}
			sources, err := c.ListSources(ctx)
			if err != nil {
				return err
			}
			byID := map[string]string{}
			kindByID := map[string]string{}
			for _, s := range sources {
				byID[s.Id.String()] = s.Name
				kindByID[s.Id.String()] = s.Type
			}
			roles, err := c.StorageByLinkRole(ctx, backing.Id)
			if err != nil {
				return err
			}
			tw := tabwriter.NewWriter(cmd.OutOrStdout(), 0, 4, 2, ' ', 0)
			fmt.Fprintln(tw, "TARGET\tKIND\tREMOTE\tLOCAL")
			for _, m := range mirrorMappings(full.ConnectionConfig) {
				id := str(m, "source_id")
				name := byID[id]
				if name == "" {
					name = id
				}
				fmt.Fprintf(tw, "%s\t%s\t%s\t%s\n", name, kindByID[id], str(m, "remote_prefix"), str(m, "local_prefix"))
			}
			fmt.Fprintln(tw)
			fmt.Fprintln(tw, "ROLE\tFILES\tBYTES")
			for _, r := range roles {
				fmt.Fprintf(tw, "%s\t%d\t%d\n", r.LinkRole, r.Count, r.TotalSize)
			}
			return tw.Flush()
		},
	}

	cmd.AddCommand(setCmd, clearCmd, showCmd)
	return cmd
}
