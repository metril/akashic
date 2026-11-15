package commands

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewUsersCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "users",
		Short: "Manage user roles",
	}

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List users and their roles",
		RunE: func(cmd *cobra.Command, args []string) error {
			users, err := c.ListUsers(context.Background())
			if err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			fmt.Fprintf(out, "%-36s  %-20s  %-8s  %-6s  %-6s  %s\n", "ID", "USERNAME", "PROVIDER", "ROLE", "PINNED", "EMAIL")
			for _, u := range users {
				email := ""
				if u.Email != nil {
					email = *u.Email
				}
				fmt.Fprintf(out, "%-36s  %-20s  %-8s  %-6s  %-6t  %s\n", u.Id, u.Username, u.AuthProvider, u.Role, u.RolePinned, email)
			}
			return nil
		},
	}

	var unpin bool
	setCmd := &cobra.Command{
		Use:   "set-role <id|username> <admin|viewer>",
		Short: "Set a user's role (pins it unless --unpin)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := context.Background()
			role := args[1]
			if role != "admin" && role != "viewer" {
				return &ExitErr{Code: ExitUserErr, Err: fmt.Errorf("role must be admin or viewer, got %q", role)}
			}
			id, err := uuid.Parse(args[0])
			if err != nil {
				users, lerr := c.ListUsers(ctx)
				if lerr != nil {
					return lerr
				}
				found := false
				for _, u := range users {
					if u.Username == args[0] {
						id, found = u.Id, true
						break
					}
				}
				if !found {
					return &ExitErr{Code: ExitUserErr, Err: fmt.Errorf("no user %q", args[0])}
				}
			}
			u, err := c.SetUserRole(ctx, id, role, unpin)
			if err != nil {
				return fmt.Errorf("set role: %w", err)
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s is now %s (pinned: %t)\n", u.Username, u.Role, u.RolePinned)
			return nil
		},
	}
	setCmd.Flags().BoolVar(&unpin, "unpin", false, "Leave the role under IdP group mapping control")

	cmd.AddCommand(listCmd, setCmd)
	return cmd
}
