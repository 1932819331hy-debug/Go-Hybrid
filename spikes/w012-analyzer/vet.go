package analyzer

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// RunVet is the supported cache-safe entry point for the W-012 go/analysis
// spike. Stock `go vet -vettool=...` does not know that arbitrary sibling .hy
// files are analyzer inputs, so its action cache cannot key on them. The
// wrapper therefore forces a fresh vet action (-a) and supplies an immutable
// .hy snapshot that the analyzer validates while reading each source.
func RunVet(ctx context.Context, args []string, stdout, stderr io.Writer) error {
	for _, arg := range args {
		if arg == "-vettool" || strings.HasPrefix(arg, "-vettool=") {
			return errorsVet("do not pass -vettool to hybvet vet; the wrapper owns the analyzer binary")
		}
	}

	cwd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("resolve workspace root: %w", err)
	}
	snapshot, err := CaptureHybridSnapshot(cwd)
	if err != nil {
		return fmt.Errorf("capture .hy snapshot: %w", err)
	}
	encoded, err := snapshot.Encode()
	if err != nil {
		return fmt.Errorf("encode .hy snapshot: %w", err)
	}

	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve hybvet executable: %w", err)
	}

	vetArgs := []string{"vet", "-a", "-vettool=" + exe}
	vetArgs = append(vetArgs, args...)
	cmd := exec.CommandContext(ctx, "go", vetArgs...)
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.Env = append(os.Environ(), hybridSnapshotEnv+"="+encoded)

	runErr := cmd.Run()
	verifyErr := snapshot.VerifyCurrent()
	if verifyErr != nil {
		return fmt.Errorf("hybrid input snapshot invalidated: %w", verifyErr)
	}
	if runErr != nil {
		return runErr
	}
	return nil
}

type vetUsageError string

func (e vetUsageError) Error() string { return string(e) }

func errorsVet(msg string) error { return vetUsageError(msg) }
