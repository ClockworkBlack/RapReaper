//go:build !windows

package ort

import "github.com/ebitengine/purego"

func loadLibrary(path string) (uintptr, error) {
	return purego.Dlopen(path, purego.RTLD_NOW|purego.RTLD_GLOBAL)
}

func findSymbol(h uintptr, name string) (uintptr, error) {
	return purego.Dlsym(h, name)
}
