package config

import (
	"os"
)

type Config struct {
	APIUrl    string
	APIKey    string
	BatchSize int
}

func Load() *Config {
	apiUrl := os.Getenv("AKASHIC_API_URL")
	if apiUrl == "" {
		apiUrl = "http://localhost:8000"
	}
	return &Config{
		APIUrl:    apiUrl,
		APIKey:    os.Getenv("AKASHIC_API_KEY"),
		BatchSize: 1000,
	}
}
