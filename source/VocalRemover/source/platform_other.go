//go:build !windows && !darwin

package main

import (
	"fmt"
	"os"
	"os/exec"
	"syscall"
)

func openBrowser(url string) error { return exec.Command("xdg-open", url).Start() }

func openFolder(dir string) error { return exec.Command("xdg-open", dir).Start() }

func setConsoleTitle(string) {}

func keepAwake(bool) {}

func exclusivePort(network, address string, c syscall.RawConn) error { return nil }

func canPickFolder() bool { return false }

func runFolderPicker(startDir string) int {
	fmt.Fprintln(os.Stderr, "the folder chooser is only available on Windows")
	return 2
}
