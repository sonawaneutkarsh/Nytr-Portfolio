#!/usr/bin/env bash
# ============================================================================
# Native validation harness (non-Xcode) — M5 fix pass.
#
# Purpose: give the UI/App production layer (HealthSyncViewModel.swift,
# CompanionApp.swift, HealthSyncView.swift) and every other production source
# the same static/typechecking coverage they previously escaped when only
# domain files were compiled by hand. This is a TYPECHECK gate — it never
# replaces building/testing in Xcode (iOS simulator runs, XCTest execution).
#
# Stages:
#   1. Typecheck ALL production sources in one module (macOS SDK; HealthKit-
#      gated code falls back to the documented non-HealthKit adapter path).
#   2. Emit the production module so test sources can import it (@testable).
#   3. Emit the harness-only XCTest shim module (CLT lacks real XCTest), then
#      typecheck EVERY test source against production symbols.
#
# Requirements: Apple Command Line Tools (swiftc) OR full Xcode. No simulator.
# Usage: ios/Harness/run_harness.sh            (from repo root or anywhere)
# Exit code: number of failed stages (0 == all green).
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/../NutritionHealthCompanion" && pwd)"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nhc-harness.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT

# Bash 3.2 compatible array population (no mapfile).
PROD_SOURCES=()
TEST_SOURCES=()
while IFS= read -r f; do PROD_SOURCES+=("$f"); done < <(find \
    "$APP_ROOT/App" "$APP_ROOT/Auth" "$APP_ROOT/Features" "$APP_ROOT/HealthKit" \
    "$APP_ROOT/Networking" "$APP_ROOT/Support" "$APP_ROOT/Sync" \
    -name '*.swift' | sort)
while IFS= read -r f; do TEST_SOURCES+=("$f"); done < <(find \
    "$APP_ROOT/HealthSyncTests" -name '*.swift' | sort)

SWIFTC=(xcrun swiftc -swift-version 5)

FAILED=0

stage() {
    local name="$1"; shift
    echo "────────────────────────────────────────────────────────"
    echo "[Harness] STAGE: $name"
    if "$@" >"$BUILD_DIR/stage.log" 2>&1; then
        echo "[Harness] PASS: $name"
    else
        local rc=$?
        echo "[Harness] FAIL: $name (exit $rc)"
        sed 's/^/    /' "$BUILD_DIR/stage.log"
        FAILED=$((FAILED + 1))
    fi
}

echo "[Harness] Compiler: $(xcrun swiftc --version | head -n1)"
echo "[Harness] Production sources: ${#PROD_SOURCES[@]}   Test sources: ${#TEST_SOURCES[@]}"
REQUIRED_FILES=(
    "$APP_ROOT/App/CompanionApp.swift"
    "$APP_ROOT/Features/HealthSync/HealthSyncViewModel.swift"
    "$APP_ROOT/Features/HealthSync/HealthSyncView.swift"
    "$APP_ROOT/Sync/HealthSyncEngine.swift"
    "$APP_ROOT/Sync/HealthSyncCoordinator.swift"
)
for f in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "[Harness] ABORT: required source missing: $f"
        exit 2
    fi
done

# Stage 1 — whole-module typecheck of production code, App/UI layer included.
stage "typecheck-production (incl. App/UI layer)" \
    "${SWIFTC[@]}" -typecheck "${PROD_SOURCES[@]}"

# Stage 2 — emit the production module for @testable imports (-enable-testing
# mirrors how the Xcode unit-test bundle consumes the app target).
stage "emit-production-module" \
    "${SWIFTC[@]}" -enable-testing -emit-module \
    -module-name NutritionHealthCompanion \
    -emit-module-path "$BUILD_DIR/NutritionHealthCompanion.swiftmodule" \
    "${PROD_SOURCES[@]}"

# Stage 3a — emit the harness-only XCTest shim under CLT (no real XCTest here).
if [[ -f "$SCRIPT_DIR/XCTestShim.swift" ]]; then
    stage "emit-xctest-shim (harness-only, typecheck surface)" \
        "${SWIFTC[@]}" -emit-module -module-name XCTest \
        -emit-module-path "$BUILD_DIR/XCTest.swiftmodule" \
        "$SCRIPT_DIR/XCTestShim.swift"

    # Stage 3b — full-suite typecheck of every regression/unit test against
    # real production symbols.
    stage "typecheck-tests against production module" \
        "${SWIFTC[@]}" -typecheck \
        -I "$BUILD_DIR" "${TEST_SOURCES[@]}"
else
    echo "[Harness] NOTE: XCTestShim.swift absent — skipping test typechecking."
fi

echo "────────────────────────────────────────────────────────"
if (( FAILED == 0 )); then
    echo "[Harness] RESULT: ALL STAGES PASSED"
else
    echo "[Harness] RESULT: $FAILED stage(s) failed"
fi
exit "$FAILED"
