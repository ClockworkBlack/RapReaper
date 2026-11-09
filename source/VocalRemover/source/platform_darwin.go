//go:build darwin

package main

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
)

// openBrowser shows the program window in the default browser (Safari unless changed).
func openBrowser(url string) error { return exec.Command("/usr/bin/open", url).Start() }

// openFolder shows a folder in the Finder.
func openFolder(dir string) error { return exec.Command("/usr/bin/open", dir).Start() }

// setConsoleTitle names the Terminal window.
func setConsoleTitle(title string) { fmt.Printf("\033]0;%s\007", title) }

// keepAwake asks macOS not to sleep while a song is being processed, through the system's own
// caffeinate tool, which holds the assertion only as long as this program runs.
var (
	awakeMu  sync.Mutex
	awakeCmd *exec.Cmd
)

func keepAwake(on bool) {
	awakeMu.Lock()
	defer awakeMu.Unlock()
	if on && awakeCmd == nil {
		cmd := exec.Command("/usr/bin/caffeinate", "-i", "-w", strconv.Itoa(os.Getpid()))
		if err := cmd.Start(); err == nil {
			awakeCmd = cmd
		}
		return
	}
	if !on && awakeCmd != nil {
		awakeCmd.Process.Kill()
		awakeCmd.Wait()
		awakeCmd = nil
	}
}

// exclusivePort: macOS does not let a second program bind an address that is already bound
// unless both ask for SO_REUSEPORT, which this program does not; nothing to do.
func exclusivePort(network, address string, c syscall.RawConn) error { return nil }

func canPickFolder() bool { return true }

// runFolderPicker shows the standard macOS folder chooser (through AppleScript) and prints the
// chosen folder. Exit code 0 = chosen, 1 = cancelled, 2 = failed.
func runFolderPicker(startDir string) int {
	script := `POSIX path of (choose folder with prompt "Choose the folder to save the instrumental and vocal files into")`
	if startDir != "" && !strings.ContainsAny(startDir, "\"\\\n\r") {
		if st, err := os.Stat(startDir); err == nil && st.IsDir() {
			script = fmt.Sprintf(`POSIX path of (choose folder with prompt "Choose the folder to save the instrumental and vocal files into" default location POSIX file "%s")`, startDir)
		}
	}
	out, err := exec.Command("/usr/bin/osascript", "-e", script).Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok && strings.Contains(string(ee.Stderr), "-128") {
			return 1 // the person cancelled
		}
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	path := strings.TrimSpace(string(out))
	path = strings.TrimSuffix(path, "/")
	if path == "" {
		return 1
	}
	fmt.Print(path)
	return 0
}
