package main

import (
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
		os.Exit(1)
	}

	c := client.New(apiURL, apiKey)

	rootCmd := &cobra.Command{
		Use:   "akashic",
		Short: "Akashic - Universal File Index",
	}

	rootCmd.AddCommand(commands.NewSearchCmd(c))
	rootCmd.AddCommand(commands.NewSourcesCmd(c))
	rootCmd.AddCommand(commands.NewScanCmd(c))
	rootCmd.AddCommand(commands.NewDuplicatesCmd(c))
	rootCmd.AddCommand(commands.NewTagCmd(c))
	rootCmd.AddCommand(commands.NewPurgeCmd(c))

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
