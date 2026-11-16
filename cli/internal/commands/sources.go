package commands

import (
	"context"
	"fmt"
	"text/tabwriter"

	"github.com/google/uuid"
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

// mirrorMappings reads connection_config.mirrors as generic maps.
func mirrorMappings(cfg map[string]any) []map[string]any {
	raw, _ := cfg["mirrors"].([]any)
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
		Short: "Link backing-source files to an Immich/Paperless app source's entries",
	}

	var backingFlag, remotePrefix, localPrefix string
	setCmd := &cobra.Command{
		Use:   "set <app>",
		Short: "Map the app's remote path prefix onto a backing source",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			app, err := c.ResolveSource(ctx, args[0])
			if err != nil {
				return err
			}
			backing, err := c.ResolveSource(ctx, backingFlag)
			if err != nil {
				return err
			}
			full, err := c.GetSource(ctx, app.Id)
			if err != nil {
				return err
			}
			bid := backing.Id.String()
			entry := map[string]any{"source_id": bid, "remote_prefix": remotePrefix, "local_prefix": localPrefix}
			list := []any{}
			placed := false
			for _, m := range mirrorMappings(full.ConnectionConfig) {
				if str(m, "source_id") != bid {
					list = append(list, m)
				} else if !placed {
					list = append(list, entry)
					placed = true
				}
			}
			if !placed {
				list = append(list, entry)
			}
			updated, err := c.UpdateSourceConfig(ctx, app.Id, map[string]any{"mirrors": list})
			if err != nil {
				return err
			}
			var rp, lp string
			found := false
			for _, m := range mirrorMappings(updated.ConnectionConfig) {
				if str(m, "source_id") == bid {
					rp, lp, found = str(m, "remote_prefix"), str(m, "local_prefix"), true
				}
			}
			if !found {
				return fmt.Errorf("server response has no mirror mapping for %s", backing.Name)
			}
			fmt.Fprintf(cmd.OutOrStdout(), "Mirror set: %s → %s (remote %q → local %q)\n", app.Name, backing.Name, rp, lp)
			return nil
		},
	}
	setCmd.Flags().StringVar(&backingFlag, "backing", "", "Backing source holding the files (name or id, required)")
	setCmd.Flags().StringVar(&remotePrefix, "remote-prefix", "", "Path prefix as the app sees it (default: empty)")
	setCmd.Flags().StringVar(&localPrefix, "local-prefix", "", "Matching path prefix in the backing source (required)")
	_ = setCmd.MarkFlagRequired("backing")
	_ = setCmd.MarkFlagRequired("local-prefix")

	var clearBacking string
	clearCmd := &cobra.Command{
		Use:   "clear <app>",
		Short: "Remove one backing's mapping, or all mappings without --backing",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			app, err := c.ResolveSource(ctx, args[0])
			if err != nil {
				return err
			}
			list := []any{}
			var backingName string
			if clearBacking != "" {
				backing, err := c.ResolveSource(ctx, clearBacking)
				if err != nil {
					return err
				}
				full, err := c.GetSource(ctx, app.Id)
				if err != nil {
					return err
				}
				backingName = backing.Name
				removed := false
				for _, m := range mirrorMappings(full.ConnectionConfig) {
					if str(m, "source_id") != backing.Id.String() {
						list = append(list, m)
					} else {
						removed = true
					}
				}
				if !removed {
					return fmt.Errorf("%s has no mirror mapping for %s", app.Name, backing.Name)
				}
			}
			if _, err := c.UpdateSourceConfig(ctx, app.Id, map[string]any{"mirrors": list}); err != nil {
				return err
			}
			if clearBacking != "" {
				fmt.Fprintf(cmd.OutOrStdout(), "Mirror cleared: %s → %s\n", app.Name, backingName)
			} else {
				fmt.Fprintf(cmd.OutOrStdout(), "Mirror cleared: %s (all backings)\n", app.Name)
			}
			return nil
		},
	}
	clearCmd.Flags().StringVar(&clearBacking, "backing", "", "Only clear this backing (name or id)")

	showCmd := &cobra.Command{
		Use:   "show <app>",
		Short: "Show mirror mappings and the per-role storage breakdown per backing",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			app, err := c.ResolveSource(ctx, args[0])
			if err != nil {
				return err
			}
			full, err := c.GetSource(ctx, app.Id)
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
			name := func(id string) string {
				if n := byID[id]; n != "" {
					return n
				}
				return id
			}
			maps := mirrorMappings(full.ConnectionConfig)
			out := cmd.OutOrStdout()
			tw := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
			fmt.Fprintln(tw, "BACKING\tTYPE\tREMOTE\tLOCAL")
			for _, m := range maps {
				id := str(m, "source_id")
				fmt.Fprintf(tw, "%s\t%s\t%s\t%s\n", name(id), kindByID[id], str(m, "remote_prefix"), str(m, "local_prefix"))
			}
			if err := tw.Flush(); err != nil {
				return err
			}
			seen := map[string]bool{}
			for _, m := range maps {
				id := str(m, "source_id")
				if seen[id] {
					continue
				}
				seen[id] = true
				bid, err := uuid.Parse(id)
				if err != nil {
					return fmt.Errorf("invalid backing id %q in mirrors: %w", id, err)
				}
				roles, err := c.StorageByLinkRole(ctx, bid)
				if err != nil {
					return err
				}
				fmt.Fprintf(out, "\n%s:\n", name(id))
				rw := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
				fmt.Fprintln(rw, "ROLE\tFILES\tBYTES")
				for _, r := range roles {
					fmt.Fprintf(rw, "%s\t%d\t%d\n", r.LinkRole, r.Count, r.TotalSize)
				}
				if err := rw.Flush(); err != nil {
					return err
				}
			}
			return nil
		},
	}

	cmd.AddCommand(setCmd, clearCmd, showCmd)
	return cmd
}
