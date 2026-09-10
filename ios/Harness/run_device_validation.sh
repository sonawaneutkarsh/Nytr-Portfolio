#!/usr/bin/env bash
# Real iOS SDK / simulator validation for a host with full Xcode installed.
#
# This deliberately fails closed when only Command Line Tools are available.
# A successful run regenerates the project, verifies the auth URL scheme and
# HealthKit entitlement, builds the app/test targets against the iOS SDK, and
# executes XCTest on an available iPhone simulator. The iOS build compiles the
# real `canImport(HealthKit)` implementation; it does not prove HealthKit
# behavior on physical hardware.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IOS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$IOS_ROOT/NutritionHealthCompanion/Project"
PROJECT_FILE="$PROJECT_DIR/NutritionHealthCompanion.xcodeproj"
SCHEME="NutritionHealthCompanion"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nhc-device-validation.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT

fail_unavailable() {
    echo "[Device validation] NOT AVAILABLE: $*" >&2
    exit 2
}

command -v xcodegen >/dev/null 2>&1 \
    || fail_unavailable "XcodeGen is required."
command -v xcodebuild >/dev/null 2>&1 \
    || fail_unavailable "xcodebuild is not installed."

DEVELOPER_DIR_PATH="$(xcode-select -p 2>/dev/null || true)"
[[ "$DEVELOPER_DIR_PATH" == *".app/Contents/Developer" ]] \
    || fail_unavailable "full Xcode is not selected (current: ${DEVELOPER_DIR_PATH:-none})."
xcrun --sdk iphoneos --show-sdk-path >/dev/null 2>&1 \
    || fail_unavailable "the iOS SDK is unavailable."
xcrun --find simctl >/dev/null 2>&1 \
    || fail_unavailable "simctl is unavailable."

echo "[Device validation] Xcode: $(xcodebuild -version | tr '\n' ' ')"
echo "[Device validation] Regenerating Xcode project"
xcodegen generate --spec "$PROJECT_DIR/project.yml"

/usr/libexec/PlistBuddy -c \
    'Print :CFBundleURLTypes:0:CFBundleURLSchemes:0' \
    "$PROJECT_DIR/Info.plist" | grep -Fxq 'nutritionhealthcompanion' \
    || fail_unavailable "the required auth callback URL scheme is missing."
/usr/libexec/PlistBuddy -c 'Print :com.apple.developer.healthkit' \
    "$PROJECT_DIR/NutritionHealthCompanion.entitlements" | grep -Fxq 'true' \
    || fail_unavailable "the HealthKit entitlement is missing."

SIMULATOR_UDID="$({ xcrun simctl list devices available --json; } | /usr/bin/python3 -c '
import json
import sys

payload = json.load(sys.stdin)
for devices in payload.get("devices", {}).values():
    for device in devices:
        if device.get("isAvailable") and device.get("name", "").startswith("iPhone"):
            print(device["udid"])
            raise SystemExit(0)
raise SystemExit(1)
' 2>/dev/null || true)"
[[ -n "$SIMULATOR_UDID" ]] \
    || fail_unavailable "no available iPhone simulator runtime/device was found."

DESTINATION="platform=iOS Simulator,id=$SIMULATOR_UDID"
COMMON_ARGS=(
    -project "$PROJECT_FILE"
    -scheme "$SCHEME"
    -configuration Debug
    -destination "$DESTINATION"
    -derivedDataPath "$BUILD_DIR/DerivedData"
    CODE_SIGNING_ALLOWED=NO
)

echo "[Device validation] Building app and test targets for $DESTINATION"
xcodebuild "${COMMON_ARGS[@]}" build

echo "[Device validation] Running XCTest for $DESTINATION"
xcodebuild "${COMMON_ARGS[@]}" test

echo "[Device validation] PASSED: iOS SDK build and simulator XCTest"
echo "[Device validation] NOTE: physical-device HealthKit behavior remains unverified."
