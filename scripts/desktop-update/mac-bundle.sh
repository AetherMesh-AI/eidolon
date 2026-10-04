#!/bin/bash
# Discovery only: the caller owns signing, swapping, and launching the bundle.
find_macos_update_bundle() { # release root, machine architecture -> bundle path
  local release="$1" arch_dir name c rebuilt=""
  case "$2" in
    arm64|aarch64) arch_dir=mac-arm64 ;;
    x86_64|amd64|x64) arch_dir=mac ;;
    *) return 1 ;;
  esac
  # Match CLI discovery: native/universal only, current brand before legacy,
  # newest compatible executable. Reject incomplete/non-executable output.
  for name in Eidolon Hermes; do
    for c in "$release/$arch_dir/$name.app" "$release/mac-universal/$name.app"; do
      [ -f "$c/Contents/MacOS/$name" ] && [ -x "$c/Contents/MacOS/$name" ] || continue
      if [ -z "$rebuilt" ] || [ "$c/Contents/MacOS/$name" -nt "$rebuilt/Contents/MacOS/$name" ]; then
        rebuilt="$c"
      fi
    done
    [ -n "$rebuilt" ] && { printf '%s\n' "$rebuilt"; return 0; }
  done
  return 1
}
