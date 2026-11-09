//go:build windows

package ort

import (
	"fmt"
	"path/filepath"
	"sync"
	"syscall"
	"unsafe"
)

const (
	searchDLLLoadDir = 0x00000100 // LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
	searchSystem32   = 0x00000800 // LOAD_LIBRARY_SEARCH_SYSTEM32
)

var (
	kernel32       = syscall.NewLazyDLL("kernel32.dll") // a "known" library: always the system's own copy
	loadLibraryExW = kernel32.NewProc("LoadLibraryExW")
	getSystemDirW  = kernel32.NewProc("GetSystemDirectoryW")
	preloadOnce    sync.Once
)

// systemLibraries are Windows libraries that ONNX Runtime, DirectML or the graphics driver load by name
// and that are not on Windows' list of "known" libraries, so a load by name would look in the program's
// own folder (or the gpu folder) first, and some of them are loaded late with the default search order.
// Loading them once from the Windows system folder, by full path, before ONNX Runtime makes every later
// load by name use that copy (Windows reuses a library that is already loaded under the same name).
var systemLibraries = []string{"dbghelp.dll", "dxgi.dll", "d3d12.dll", "dxcore.dll"}

func preloadSystemLibraries() {
	buf := make([]uint16, 512)
	n, _, _ := getSystemDirW.Call(uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if n == 0 || int(n) >= len(buf) {
		return
	}
	dir := syscall.UTF16ToString(buf[:n])
	for _, name := range systemLibraries {
		p, err := syscall.UTF16PtrFromString(filepath.Join(dir, name))
		if err != nil {
			continue
		}
		// A library this version of Windows does not have (dxcore.dll on older systems) is simply
		// skipped; whatever needs it reports that itself.
		loadLibraryExW.Call(uintptr(unsafe.Pointer(p)), 0, searchSystem32)
	}
}

// loadLibrary loads the DLL at exactly this path. The libraries it depends on are looked for
// only in the DLL's own folder and in the Windows system folder: not in the current folder
// and not along PATH, where a stray or planted file of the same name could be picked up.
func loadLibrary(path string) (uintptr, error) {
	if !filepath.IsAbs(path) {
		return 0, fmt.Errorf("library path must be absolute")
	}
	preloadOnce.Do(preloadSystemLibraries)
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return 0, err
	}
	h, _, callErr := loadLibraryExW.Call(uintptr(unsafe.Pointer(p)), 0, searchDLLLoadDir|searchSystem32)
	if h == 0 {
		return 0, callErr
	}
	return h, nil
}

func findSymbol(h uintptr, name string) (uintptr, error) {
	return syscall.GetProcAddress(syscall.Handle(h), name)
}
