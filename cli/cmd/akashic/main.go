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

	c := client.New(apiURL, apiKey)

	rootCmd := &cobra.Command{
		Use:   "akashic",
		Short: "Akashic - Universal File Index",
	}

	rootCmd.AddCommand(commands.NewSearchCmd(c))
	rootCmd.AddCommand(commands.NewSourcesCmd(c))

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
