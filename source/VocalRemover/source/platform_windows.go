//go:build windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"unsafe"
)

// openBrowser shows the program window. Microsoft Edge (part of Windows 10) can show a page
// as a plain application window without tabs or address bar; if Edge is not found, the page
// opens in the default browser instead.
func openBrowser(url string) error {
	for _, base := range []string{os.Getenv("ProgramFiles(x86)"), os.Getenv("ProgramFiles"), os.Getenv("LocalAppData")} {
		if base == "" {
			continue
		}
		edge := filepath.Join(base, "Microsoft", "Edge", "Application", "msedge.exe")
		if st, err := os.Stat(edge); err == nil && !st.IsDir() {
			if err := exec.Command(edge, "--app="+url, "--window-size=1040,900").Start(); err == nil {
				return nil
			}
		}
	}
	return exec.Command(filepath.Join(os.Getenv("SystemRoot"), "System32", "rundll32.exe"), "url.dll,FileProtocolHandler", url).Start()
}

func openFolder(dir string) error {
	if strings.ContainsAny(dir, "\"\r\n") {
		return fmt.Errorf("folder name cannot be passed to Explorer")
	}
	explorer := filepath.Join(os.Getenv("SystemRoot"), "explorer.exe")
	cmd := exec.Command(explorer)
	// Explorer has its own command-line rules (a comma separates options), so the folder is
	// always passed inside quotes rather than relying on automatic quoting.
	cmd.SysProcAttr = &syscall.SysProcAttr{CmdLine: `"` + explorer + `" "` + dir + `"`}
	return cmd.Start()
}

var kernel32 = syscall.NewLazyDLL("kernel32.dll")

// setConsoleTitle names the console window and switches off its "QuickEdit" mode, in which
// an accidental click in the window freezes the program until a key is pressed.
func setConsoleTitle(title string) {
	if p, err := syscall.UTF16PtrFromString(title); err == nil {
		kernel32.NewProc("SetConsoleTitleW").Call(uintptr(unsafe.Pointer(p)))
	}
	const (
		enableQuickEditMode = 0x0040
		enableExtendedFlags = 0x0080
	)
	if in, err := syscall.GetStdHandle(syscall.STD_INPUT_HANDLE); err == nil {
		var mode uint32
		if syscall.GetConsoleMode(in, &mode) == nil {
			kernel32.NewProc("SetConsoleMode").Call(uintptr(in), uintptr(mode&^enableQuickEditMode|enableExtendedFlags))
		}
	}
}

// keepAwake asks Windows not to go to sleep while a song is being processed. The request
// belongs to one operating-system thread, so a dedicated thread makes both calls.
var awakeRequests = func() chan bool {
	ch := make(chan bool, 4)
	go func() {
		runtime.LockOSThread()
		const (
			esContinuous     = 0x80000000
			esSystemRequired = 0x00000001
		)
		proc := kernel32.NewProc("SetThreadExecutionState")
		for on := range ch {
			if on {
				proc.Call(esContinuous | esSystemRequired)
			} else {
				proc.Call(esContinuous)
			}
		}
	}()
	return ch
}()

func keepAwake(on bool) {
	select {
	case awakeRequests <- on:
	default:
	}
}

// exclusivePort stops any other program from attaching itself to the same local port.
func exclusivePort(network, address string, c syscall.RawConn) error {
	const soExclusiveAddrUse = -5 // ^SO_REUSEADDR
	c.Control(func(fd uintptr) {
		syscall.SetsockoptInt(syscall.Handle(fd), syscall.SOL_SOCKET, soExclusiveAddrUse, 1)
	})
	return nil
}

func canPickFolder() bool { return true }

// browseInfo mirrors the Windows BROWSEINFOW structure.
type browseInfo struct {
	owner       uintptr
	root        uintptr
	displayName *uint16
	title       *uint16
	flags       uint32
	callback    uintptr
	lParam      uintptr
	image       int32
}

// runFolderPicker shows the standard Windows "Browse for folder" dialog and prints the
// chosen folder. Exit code 0 = chosen, 1 = cancelled, 2 = failed.
func runFolderPicker(startDir string) int {
	runtime.LockOSThread()
	shell32 := syscall.NewLazyDLL("shell32.dll")
	ole32 := syscall.NewLazyDLL("ole32.dll")
	user32 := syscall.NewLazyDLL("user32.dll")
	for _, d := range []*syscall.LazyDLL{shell32, ole32, user32} {
		if err := d.Load(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 2
		}
	}
	const (
		coinitApartmentThreaded = 0x2
		bifReturnOnlyFSDirs     = 0x1
		bifEditBox              = 0x10
		bifNewDialogStyle       = 0x40
		bffmInitialized         = 1
		bffmSetSelectionW       = 0x400 + 103
	)
	ole32.NewProc("CoInitializeEx").Call(0, coinitApartmentThreaded)
	owner, _, _ := user32.NewProc("GetForegroundWindow").Call()
	title, _ := syscall.UTF16PtrFromString("Choose the folder to save the instrumental and vocal files into")
	display := make([]uint16, 1024)
	bi := browseInfo{owner: owner, displayName: &display[0], title: title, flags: bifReturnOnlyFSDirs | bifEditBox | bifNewDialogStyle}
	if startDir != "" {
		if st, err := os.Stat(startDir); err == nil && st.IsDir() {
			if start, err := syscall.UTF16PtrFromString(startDir); err == nil {
				sendMessage := user32.NewProc("SendMessageW")
				bi.lParam = uintptr(unsafe.Pointer(start))
				bi.callback = syscall.NewCallback(func(hwnd uintptr, msg uint32, lParam, data uintptr) uintptr {
					if msg == bffmInitialized && data != 0 {
						sendMessage.Call(hwnd, bffmSetSelectionW, 1, data)
					}
					return 0
				})
				defer runtime.KeepAlive(start)
			}
		}
	}
	pidl, _, _ := shell32.NewProc("SHBrowseForFolderW").Call(uintptr(unsafe.Pointer(&bi)))
	runtime.KeepAlive(title)
	if pidl == 0 {
		return 1
	}
	defer ole32.NewProc("CoTaskMemFree").Call(pidl)
	path := make([]uint16, 32768)
	ok, _, _ := shell32.NewProc("SHGetPathFromIDListW").Call(pidl, uintptr(unsafe.Pointer(&path[0])))
	if uint32(ok) == 0 { // the function returns a 32-bit BOOL
		return 2
	}
	fmt.Print(syscall.UTF16ToString(path))
	return 0
}
