package commands

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/apiclient"
	"github.com/akashic-project/akashic/cli/internal/client"
)

// syncPollInterval is how often `access sync --wait` polls the status.
var syncPollInterval = 2 * time.Second

func NewAccessCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "access",
		Short: "Find people and groups and see what they can reach",
	}

	var whoKind string
	var whoLimit int
	whoCmd := &cobra.Command{
		Use:   "who <query>",
		Short: "Search people and groups across AD, Authentik and akashic users",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			kind, err := identityKind(whoKind)
			if err != nil {
				return err
			}
			res, err := c.SearchIdentities(context.Background(), args[0], kind, whoLimit)
			if err != nil {
				return classifyAPIError(err)
			}
			out := cmd.OutOrStdout()
			fmt.Fprintf(out, "%-32s  %-8s  %-24s  %-16s  %s\n", "ID", "KIND", "NAME", "BACKENDS", "NOTES")
			for _, r := range res.Results {
				fmt.Fprintf(out, "%-32s  %-8s  %-24s  %-16s  %s\n", r.Id, r.Kind, identityName(r), backendList(r), identityNotes(r))
			}
			for _, b := range res.BackendsStatus {
				if b.Status == apiclient.BackendStatusStatusOk {
					continue
				}
				line := fmt.Sprintf("%s: %s", b.Backend, b.Status)
				if b.Detail != nil && *b.Detail != "" {
					line += fmt.Sprintf(" (%s)", *b.Detail)
				}
				fmt.Fprintln(out, line)
			}
			return nil
		},
	}
	whoCmd.Flags().StringVar(&whoKind, "kind", "any", "any, person or group")
	whoCmd.Flags().IntVar(&whoLimit, "limit", 0, "Maximum results (server default when 0)")

	var showRight string
	var showLimit int
	showCmd := &cobra.Command{
		Use:   "show <id|query>",
		Short: "Show everything an identity can reach (an argument with ':' is an id, anything else a search)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			id := args[0]
			if !strings.Contains(id, ":") {
				res, err := c.SearchIdentities(ctx, id, "", 0)
				if err != nil {
					return classifyAPIError(err)
				}
				id, err = pickIdentity(id, res.Results)
				if err != nil {
					return err
				}
			}
			resp, err := c.AccessByIdentity(ctx, id, showRight, showLimit)
			if err != nil {
				return classifyAPIError(err)
			}
			printAccess(cmd.OutOrStdout(), resp)
			return nil
		},
	}
	showCmd.Flags().StringVar(&showRight, "right", "read", "read, write or delete")
	showCmd.Flags().IntVar(&showLimit, "limit", 0, "Sample size (server default when 0)")

	var syncWait bool
	var syncTimeout time.Duration
	syncCmd := &cobra.Command{
		Use:   "sync",
		Short: "Start a directory sync (AD and Authentik)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			out := cmd.OutOrStdout()
			started, err := c.StartDirectorySync(ctx)
			if err != nil {
				var apiErr *client.APIError
				if errors.As(err, &apiErr) && apiErr.Status == 409 {
					return &ExitErr{Code: ExitUserErr, Err: fmt.Errorf("a directory sync is already running: %w", err)}
				}
				return classifyAPIError(err)
			}
			for _, r := range started.Runs {
				fmt.Fprintf(out, "started %s sync (%s)\n", r.Backend, r.Status)
			}
			if !syncWait {
				return nil
			}
			ids := make(map[string]bool, len(started.Runs))
			for _, r := range started.Runs {
				ids[r.Id.String()] = true
			}
			return waitForSync(ctx, c, out, syncTimeout, ids)
		},
	}
	syncCmd.Flags().BoolVar(&syncWait, "wait", false, "Wait for the sync to finish; exit non-zero if a run failed")
	syncCmd.Flags().DurationVar(&syncTimeout, "timeout", 10*time.Minute, "Maximum time to wait with --wait")

	syncStatusCmd := &cobra.Command{
		Use:   "sync-status",
		Short: "Show directory sync state per backend and the schedule",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			st, err := c.DirectoryStatus(context.Background())
			if err != nil {
				return classifyAPIError(err)
			}
			printSyncStatus(cmd.OutOrStdout(), st)
			return nil
		},
	}

	cmd.AddCommand(whoCmd, showCmd, syncCmd, syncStatusCmd)
	return cmd
}

func identityKind(k string) (apiclient.SearchIdentitiesRouteParamsKind, error) {
	switch apiclient.SearchIdentitiesRouteParamsKind(k) {
	case apiclient.SearchIdentitiesRouteParamsKindAny, "":
		return "", nil
	case apiclient.SearchIdentitiesRouteParamsKindPerson, apiclient.SearchIdentitiesRouteParamsKindGroup:
		return apiclient.SearchIdentitiesRouteParamsKind(k), nil
	}
	return "", &ExitErr{Code: ExitUserErr, Err: fmt.Errorf("kind must be any, person or group, got %q", k)}
}

func identityName(r apiclient.IdentityResult) string {
	switch {
	case r.DisplayName != nil && *r.DisplayName != "":
		return *r.DisplayName
	case r.Username != nil:
		return *r.Username
	}
	return "-"
}

func backendList(r apiclient.IdentityResult) string {
	names := make([]string, 0, len(r.Backends))
	for _, b := range r.Backends {
		names = append(names, string(b.Backend))
	}
	return strings.Join(names, ",")
}

func identityNotes(r apiclient.IdentityResult) string {
	var notes []string
	if r.MatchQuality == apiclient.Possible {
		note := "possible match"
		if len(r.PossibleMatchOf) > 0 {
			note += " of " + strings.Join(r.PossibleMatchOf, ",")
		}
		notes = append(notes, note)
	}
	if !r.HasFsIdentity {
		notes = append(notes, "no file-system identity")
	}
	return strings.Join(notes, "; ")
}

// pickIdentity resolves a search to exactly one exact match, else a user error.
func pickIdentity(query string, results []apiclient.IdentityResult) (string, error) {
	var exact []apiclient.IdentityResult
	for _, r := range results {
		if r.MatchQuality == apiclient.Exact {
			exact = append(exact, r)
		}
	}
	switch {
	case len(exact) == 1:
		return exact[0].Id, nil
	case len(exact) == 0:
		return "", &ExitErr{Code: ExitUserErr, Err: fmt.Errorf("no identity matches %q", query)}
	}
	ids := make([]string, len(exact))
	for i, r := range exact {
		ids[i] = r.Id
	}
	return "", &ExitErr{Code: ExitUserErr, Err: fmt.Errorf("%q matches several identities, pass one id: %s", query, strings.Join(ids, ", "))}
}

func printAccess(out io.Writer, r *apiclient.PrincipalAccessResponse) {
	if r.Identity != nil {
		fmt.Fprintf(out, "Identity: %s (%s, %s)\n", identityName(*r.Identity), r.Identity.Kind, r.Identity.Id)
	}
	fmt.Fprintf(out, "Right: %s\n", r.Right)
	fmt.Fprintf(out, "Files: %d in %d sources, %s (%d bytes)\n", r.Summary.FileCount, r.Summary.SourceCount, humanSize(r.Summary.TotalSizeBytes), r.Summary.TotalSizeBytes)
	for _, s := range r.BySource {
		fmt.Fprintf(out, "  %s: %d files\n", s.SourceName, s.FileCount)
	}
	if e := r.Expansion; e != nil {
		fmt.Fprintf(out, "Group expansion: %s", e.Status)
		if e.Status != apiclient.AccessExpansionStatusOk {
			if e.CachedAt != nil {
				fmt.Fprintf(out, " (as of %s)", e.CachedAt.Format(time.RFC3339))
			}
			if e.Detail != nil && *e.Detail != "" {
				fmt.Fprintf(out, ": %s", *e.Detail)
			}
		}
		fmt.Fprintln(out)
		if len(e.MissingIdentityKinds) > 0 {
			kinds := make([]string, len(e.MissingIdentityKinds))
			for i, k := range e.MissingIdentityKinds {
				kinds[i] = string(k)
			}
			fmt.Fprintf(out, "Missing identity: %s\n", strings.Join(kinds, ", "))
		}
	}
	if r.TokensUsed != nil && len(*r.TokensUsed) > 0 {
		fmt.Fprintln(out, "Tokens used:")
		for _, t := range *r.TokensUsed {
			name := ""
			if t.Name != nil && *t.Name != "" {
				name = "  " + *t.Name
			}
			fmt.Fprintf(out, "  %s  [%s]%s\n", t.Token, t.Origin, name)
		}
	}
	for _, w := range r.Warnings {
		fmt.Fprintf(out, "Warning: %s\n", w)
	}
	if len(r.Sample) > 0 {
		fmt.Fprintln(out, "Sample:")
		for _, h := range r.Sample {
			mark := ""
			if h.HasDenyAce != nil && *h.HasDenyAce {
				mark = "  [deny ACE]"
			}
			fmt.Fprintf(out, "  %s%s\n", h.Path, mark)
		}
	}
}

func printSyncStatus(out io.Writer, st *apiclient.DirectoryStatusResponse) {
	fmt.Fprintf(out, "%-10s  %-10s  %-8s  %-10s  %s\n", "BACKEND", "CONFIGURED", "RUNNING", "LAST RUN", "LAST SUCCESS")
	for _, b := range st.Backends {
		last, success := "-", "-"
		if b.LastRun != nil {
			last = string(b.LastRun.Status)
		}
		if b.LastSuccessAt != nil {
			success = b.LastSuccessAt.Format(time.RFC3339)
		}
		fmt.Fprintf(out, "%-10s  %-10t  %-8t  %-10s  %s\n", b.Backend, b.Configured, b.Running, last, success)
	}
	if st.IntervalMinutes == 0 {
		fmt.Fprintln(out, "Schedule: manual only")
		return
	}
	next := "-"
	if st.NextRunAt != nil {
		next = st.NextRunAt.Format(time.RFC3339)
	}
	fmt.Fprintf(out, "Schedule: every %d min, next run %s\n", st.IntervalMinutes, next)
}

// humanSize renders a byte count with a binary unit (e.g. 4.0 KiB).
func humanSize(b int) string {
	n := int64(b)
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(n)/float64(div), "KMGTPE"[exp])
}

// waitForSync polls until no backend is running, then fails if one of this
// sync's runs (ids) failed; a stale failed run elsewhere is not this sync's.
func waitForSync(ctx context.Context, c *client.Client, out io.Writer, timeout time.Duration, ids map[string]bool) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	timedOut := func() error {
		return &ExitErr{Code: ExitServerErr, Err: fmt.Errorf("timed out waiting for directory sync after %s", timeout)}
	}
	for {
		st, err := c.DirectoryStatus(ctx)
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, context.DeadlineExceeded) {
				return timedOut()
			}
			return classifyAPIError(err)
		}
		running := false
		for _, b := range st.Backends {
			running = running || b.Running
		}
		if !running {
			var failed []string
			for _, b := range st.Backends {
				if b.LastRun != nil && ids[b.LastRun.Id.String()] && b.LastRun.Status == apiclient.DirectorySyncRunOutStatusFailed {
					reason := ""
					if b.LastRun.Error != nil {
						reason = ": " + *b.LastRun.Error
					}
					failed = append(failed, fmt.Sprintf("%s%s", b.Backend, reason))
				}
			}
			if len(failed) > 0 {
				return &ExitErr{Code: ExitServerErr, Err: fmt.Errorf("directory sync failed: %s", strings.Join(failed, "; "))}
			}
			fmt.Fprintln(out, "sync finished")
			return nil
		}
		select {
		case <-ctx.Done():
			return timedOut()
		case <-time.After(syncPollInterval):
		}
	}
}
