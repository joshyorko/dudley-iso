package main

import (
	"context"
	"fmt"
	"regexp"

	"dagger/quattro-installer/internal/dagger"
)

type QuattroInstaller struct{}

// Drive the shared host-capability boundary on the already-owned Harvester.
// The socket is explicitly supplied by its remote operator; never a workstation.
func (m *QuattroInstaller) HostPhase(ctx context.Context, dockerSocket *dagger.Socket, runPath string, phase string, executionID string) (string, error) {
	if !regexp.MustCompile(`^/home/kdlocpanda/omarchy-builds/quattro-[a-zA-Z0-9.-]+$`).MatchString(runPath) {
		return "", fmt.Errorf("invalid owned run path")
	}
	switch phase {
	case "probe", "build", "live", "install", "desktop":
	default:
		return "", fmt.Errorf("invalid installer phase")
	}
	return dag.Container().From("docker:27-cli").
		WithUnixSocket("/var/run/docker.sock", dockerSocket).
		WithEnvVariable("QUATTRO_EXECUTION_ID", executionID).
		WithExec([]string{"docker", "run", "--rm", "--privileged", "--pid=host", "--network=host",
			"ubuntu:24.04", "nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--",
			"env", "OMARCHY_REMOTE_BUILDER=harvester", "bash", runPath + "/source/scripts/quattro-host.sh", runPath, phase}).
		Stdout(ctx)
}
