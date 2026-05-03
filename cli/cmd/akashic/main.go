package main

import (
	"errors"
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
	"github.com/akashic-project/akashic/cli/internal/commands"
)

func main() {
	apiURL := os.Getenv("AKASHIC_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:8000"
	}
	apiKey := os.Getenv("AKASHIC_API_KEY")
	if apiKey == "" {
		fmt.Fprintln(os.Stderr, "error: AKASHIC_API_KEY environment variable is not set")
		os.Exit(commands.ExitUserErr)
	}

	c := client.New(apiURL, apiKey)

	rootCmd := &cobra.Command{
		Use:   "akashic",
		Short: "Akashic - Universal File Index",
	}
	// Cobra prints "Error: ..." after RunE returns; we surface it
	// ourselves with a tighter prefix and translate to a meaningful
	// exit code below.
	rootCmd.SilenceErrors = true
	rootCmd.SilenceUsage = true

	rootCmd.AddCommand(commands.NewSearchCmd(c))
	rootCmd.AddCommand(commands.NewSourcesCmd(c))
	rootCmd.AddCommand(commands.NewScanCmd(c))
	rootCmd.AddCommand(commands.NewDuplicatesCmd(c))
	rootCmd.AddCommand(commands.NewTagCmd(c))
	rootCmd.AddCommand(commands.NewPurgeCmd(c))

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		var ee *commands.ExitErr
		if errors.As(err, &ee) {
			os.Exit(ee.Code)
		}
		// Default for un-classified errors (non-ExitErr returns from
		// commands that haven't been migrated yet).
		os.Exit(commands.ExitServerErr)
	}
}
