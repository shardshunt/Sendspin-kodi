#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MANIFEST="$ROOT_DIR/build/native-backend-matrix.json"

# Default configuration
CONTAINER_MODE=1
CONTAINER_ENGINE="${CONTAINER_ENGINE:-}"
CONTAINERFILE="${ROOT_DIR}/Containerfile.native-build-ubuntu"
IMAGE_NAME="localhost/sendspin-kodi-native-builder:ubuntu2204"
BUILD_IMAGE=1
CLEAN=1
BUILD_TYPE="${CMAKE_BUILD_TYPE:-Release}"
RECORD_MATRIX=1
FLAT=0
WORKER=0
PARALLEL_BUILDS=1
CLI_JOBS=""

TARGET_ARCHES=""
CLI_ARCHES=""

show_help() {
  cat <<'EOF'
Usage: scripts/build_native_backends_all.sh [OPTIONS] [ARCH...]

Build native Sendspin backend binaries for one or more target architectures.
When multiple architectures are specified, they build simultaneously in parallel
with clear progress reporting.

Architectures:
  ARCH                       One or more architectures to build: x86_64, i386, aarch64, armv7.
                             Defaults to all ('x86_64 i386 aarch64 armv7') if omitted.

Options:
  -a, --arch ARCH            Target architecture (can be specified multiple times).
      --all                  Build all supported architectures (x86_64, i386, aarch64, armv7).
      --parallel             Build architectures simultaneously in parallel (default).
      --serial, --no-parallel
                             Build architectures sequentially one after another.
  -j, --jobs JOBS            Compiler parallel jobs per architecture (default: auto-split).
      --host, --native       Build directly on the host rather than in a container.
      --container            Build inside a container (default).
      --engine ENGINE        Container engine: podman or docker (default: auto-detect).
  -f, --file, --containerfile FILE
                             Path to Containerfile (default: Containerfile.native-build-ubuntu).
  -t, --tag, --image TAG     Container image tag (default: localhost/sendspin-kodi-native-builder:ubuntu2204).
      --no-image-build       Skip building the container image before running.
      --clean                Clean build directory before each build (default).
      --no-clean             Preserve build directory across builds (faster rebuilds).
      --build-type TYPE      CMake build type (Release, Debug, RelWithDebInfo; default: Release).
      --record-matrix        Record matrix manifest when all architectures are built (default).
      --no-record-matrix     Skip recording release matrix manifest.
      --flat                 Install binary directly to resources/bin instead of
                             architecture subdirectory (single architecture only).
  -h, --help                 Display this help message and exit.

Environment Variables:
  NATIVE_ARCH                Default single architecture (legacy compatibility).
  NATIVE_ARCHES              Default list of architectures (legacy compatibility).
  CONTAINER_ENGINE           Default container engine ('podman' or 'docker').
  CMAKE_BUILD_TYPE           Default CMake build type.
EOF
}

add_cli_arch() {
  case "$1" in
    x86_64|i386|aarch64|armv7) ;;
    *)
      echo "Error: Unsupported architecture '$1'; use x86_64, i386, aarch64, or armv7" >&2
      exit 1
      ;;
  esac
  case " $CLI_ARCHES " in
    *" $1 "*)
      echo "Error: Architecture '$1' was specified more than once" >&2
      exit 1
      ;;
    *)
      CLI_ARCHES="$CLI_ARCHES $1"
      ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    -a|--arch)
      [ $# -ge 2 ] || { echo "Error: $1 requires an argument" >&2; exit 1; }
      add_cli_arch "$2"
      shift 2
      ;;
    --arch=*)
      add_cli_arch "${1#*=}"
      shift
      ;;
    --all)
      CLI_ARCHES=" x86_64 i386 aarch64 armv7"
      shift
      ;;
    --parallel)
      PARALLEL_BUILDS=1
      shift
      ;;
    --serial|--no-parallel|--sequential)
      PARALLEL_BUILDS=0
      shift
      ;;
    -j|--jobs)
      [ $# -ge 2 ] || { echo "Error: $1 requires an argument" >&2; exit 1; }
      CLI_JOBS="$2"
      shift 2
      ;;
    --jobs=*)
      CLI_JOBS="${1#*=}"
      shift
      ;;
    --host|--native)
      CONTAINER_MODE=0
      shift
      ;;
    --container)
      CONTAINER_MODE=1
      shift
      ;;
    --engine)
      [ $# -ge 2 ] || { echo "Error: $1 requires an argument" >&2; exit 1; }
      CONTAINER_ENGINE="$2"
      shift 2
      ;;
    --engine=*)
      CONTAINER_ENGINE="${1#*=}"
      shift
      ;;
    -f|--file|--containerfile)
      [ $# -ge 2 ] || { echo "Error: $1 requires an argument" >&2; exit 1; }
      CONTAINERFILE="$2"
      shift 2
      ;;
    --file=*|--containerfile=*)
      CONTAINERFILE="${1#*=}"
      shift
      ;;
    -t|--tag|--image)
      [ $# -ge 2 ] || { echo "Error: $1 requires an argument" >&2; exit 1; }
      IMAGE_NAME="$2"
      shift 2
      ;;
    --tag=*|--image=*)
      IMAGE_NAME="${1#*=}"
      shift
      ;;
    --no-image-build|--skip-image-build)
      BUILD_IMAGE=0
      shift
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --no-clean)
      CLEAN=0
      shift
      ;;
    --build-type)
      [ $# -ge 2 ] || { echo "Error: $1 requires an argument" >&2; exit 1; }
      BUILD_TYPE="$2"
      shift 2
      ;;
    --build-type=*)
      BUILD_TYPE="${1#*=}"
      shift
      ;;
    --record-matrix)
      RECORD_MATRIX=1
      shift
      ;;
    --no-record-matrix)
      RECORD_MATRIX=0
      shift
      ;;
    --flat)
      FLAT=1
      shift
      ;;
    --worker)
      WORKER=1
      CONTAINER_MODE=0
      shift
      ;;
    --)
      shift
      while [ $# -gt 0 ]; do
        add_cli_arch "$1"
        shift
      done
      break
      ;;
    -*)
      echo "Error: Unknown option '$1'. Use --help for usage." >&2
      exit 1
      ;;
    *)
      add_cli_arch "$1"
      shift
      ;;
  esac
done

case "$CONTAINERFILE" in
  /*) ;;
  *) CONTAINERFILE="$ROOT_DIR/$CONTAINERFILE" ;;
esac

if [ -n "$CLI_ARCHES" ]; then
  RAW_ARCHES="$CLI_ARCHES"
elif [ -n "${NATIVE_ARCH:-}" ]; then
  RAW_ARCHES="$NATIVE_ARCH"
elif [ -n "${NATIVE_ARCHES:-}" ]; then
  RAW_ARCHES="$NATIVE_ARCHES"
else
  RAW_ARCHES="x86_64 i386 aarch64 armv7"
fi

for arch in $RAW_ARCHES; do
  case "$arch" in
    x86_64|i386|aarch64|armv7) ;;
    *)
      echo "Error: Unsupported architecture '$arch'; use x86_64, i386, aarch64, or armv7" >&2
      exit 1
      ;;
  esac
  case " $TARGET_ARCHES " in
    *" $arch "*)
      echo "Error: Architecture '$arch' was requested more than once" >&2
      exit 1
      ;;
    *)
      TARGET_ARCHES="$TARGET_ARCHES $arch"
      ;;
  esac
done

# shellcheck disable=SC2086
TARGET_ARCHES=$(echo "$TARGET_ARCHES" | xargs)

ARCH_COUNT=0
for _ in $TARGET_ARCHES; do
  ARCH_COUNT=$((ARCH_COUNT + 1))
done

if [ "$FLAT" -eq 1 ] && [ "$ARCH_COUNT" -ne 1 ]; then
  echo "Error: --flat is only supported when building a single architecture" >&2
  exit 1
fi

# Calculate compiler jobs per architecture
TOTAL_CPUS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
if [ -n "$CLI_JOBS" ]; then
  JOBS_PER_ARCH="$CLI_JOBS"
else
  if [ "$PARALLEL_BUILDS" -eq 1 ] && [ "$ARCH_COUNT" -gt 1 ]; then
    JOBS_PER_ARCH=$(( TOTAL_CPUS / ARCH_COUNT ))
    [ "$JOBS_PER_ARCH" -ge 1 ] || JOBS_PER_ARCH=1
  else
    JOBS_PER_ARCH="$TOTAL_CPUS"
  fi
fi

# Single architecture compilation function
compile_architecture() {
  native_arch="$1"
  jobs="$2"
  clean="$3"
  flat="$4"
  btype="$5"

  build_dir="${ROOT_DIR}/build/native-${native_arch}"
  if [ "$clean" -eq 1 ]; then
    rm -rf -- "$build_dir"
  fi
  mkdir -p "$build_dir"

  cmake_cross_args=""
  host_arch=$(uname -m)
  case "$native_arch" in
    x86_64)
      case "$host_arch" in
        x86_64|amd64) ;;
        *)
          cmake_cross_args="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=x86_64 -DCMAKE_C_COMPILER=x86_64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=x86_64-linux-gnu-g++"
          export PKG_CONFIG_PATH="/usr/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
          ;;
      esac
      ;;
    i386)
      cmake_cross_args="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=i686 -DCMAKE_C_COMPILER=i686-linux-gnu-gcc -DCMAKE_CXX_COMPILER=i686-linux-gnu-g++"
      export PKG_CONFIG_LIBDIR="/usr/lib/i386-linux-gnu/pkgconfig:/usr/share/pkgconfig"
      ;;
    aarch64)
      case "$host_arch" in
        aarch64|arm64) ;;
        *)
          cmake_cross_args="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++"
          export PKG_CONFIG_PATH="/usr/lib/aarch64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
          ;;
      esac
      ;;
    armv7)
      case "$host_arch" in
        armv7*|armhf) ;;
        *)
          cmake_cross_args="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=armv7l -DCMAKE_C_COMPILER=arm-linux-gnueabihf-gcc -DCMAKE_CXX_COMPILER=arm-linux-gnueabihf-g++"
          export PKG_CONFIG_PATH="/usr/lib/arm-linux-gnueabihf/pkgconfig:${PKG_CONFIG_PATH:-}"
          ;;
      esac
      ;;
  esac

  # shellcheck disable=SC2086
  cmake -S "${ROOT_DIR}/native" -B "${build_dir}" \
    -DCMAKE_BUILD_TYPE="${btype}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_INSTALL_PREFIX="${ROOT_DIR}" \
    $cmake_cross_args

  cmake --build "${build_dir}" --parallel "$jobs"

  if [ "$flat" -eq 1 ]; then
    output_dir="${ROOT_DIR}/service.sendspin/resources/bin"
  else
    output_dir="${ROOT_DIR}/service.sendspin/resources/bin/${native_arch}"
  fi
  mkdir -p "$output_dir"
  install -m 0755 \
    "${build_dir}/sendspin-kodi-backend" \
    "${output_dir}/sendspin-kodi-backend"

  printf 'Packaged native backend at %s\n' \
    "${output_dir}/sendspin-kodi-backend"
}

# Helpers for monitoring progress
render_progress_bar() {
  pct="$1"
  bar_width=14
  filled=$(( pct * bar_width / 100 ))
  empty=$(( bar_width - filled ))
  bar=""
  i=0
  while [ $i -lt $filled ]; do
    bar="${bar}#"
    i=$((i + 1))
  done
  i=0
  while [ $i -lt $empty ]; do
    bar="${bar}-"
    i=$((i + 1))
  done
  printf "[%s] %3d%%" "$bar" "$pct"
}

get_build_status() {
  arch="$1"
  log_file="$ROOT_DIR/build/build-${arch}.log"
  if [ ! -f "$log_file" ]; then
    printf 'Starting...'
    return
  fi

  latest_pct=$(grep -o '\[ *[0-9]\{1,3\}%\]' "$log_file" 2>/dev/null | tail -n 1 || true)
  if [ -n "$latest_pct" ]; then
    pct_num=$(echo "$latest_pct" | tr -d '[% ]')
    bar=$(render_progress_bar "$pct_num")
    printf '%s Compiling' "$bar"
    return
  fi

  if grep -q "Generating done" "$log_file" 2>/dev/null; then
    printf '[--------------]   0%% Compiling'
    return
  fi

  if grep -q "The CXX compiler identification" "$log_file" 2>/dev/null; then
    printf 'Configuring CMake...'
    return
  fi

  printf 'Initializing build...'
}

# Container execution branch
if [ "$CONTAINER_MODE" -eq 1 ]; then
  if [ -z "$CONTAINER_ENGINE" ]; then
    if command -v podman >/dev/null 2>&1; then
      CONTAINER_ENGINE="podman"
    elif command -v docker >/dev/null 2>&1; then
      CONTAINER_ENGINE="docker"
    else
      echo "Error: Neither podman nor docker found in PATH. Install a container engine or pass --host to build directly on host." >&2
      exit 1
    fi
  else
    if ! command -v "$CONTAINER_ENGINE" >/dev/null 2>&1; then
      echo "Error: Container engine '$CONTAINER_ENGINE' not found in PATH." >&2
      exit 1
    fi
  fi

  if [ ! -f "$CONTAINERFILE" ]; then
    echo "Error: Containerfile not found at '$CONTAINERFILE'" >&2
    exit 1
  fi

  if [ "$BUILD_IMAGE" -eq 1 ]; then
    echo "=== Building container image: $IMAGE_NAME ==="
    "$CONTAINER_ENGINE" build \
      --platform linux/amd64 \
      --tag "$IMAGE_NAME" \
      --file "$CONTAINERFILE" \
      "$ROOT_DIR"
  fi

  RUN_FLAGS="--rm --platform linux/amd64"
  if [ -t 1 ]; then
    RUN_FLAGS="$RUN_FLAGS -t"
  fi
  if [ "$CONTAINER_ENGINE" = "podman" ]; then
    RUN_FLAGS="$RUN_FLAGS --userns=keep-id --volume ${ROOT_DIR}:/workspace:Z --workdir /workspace"
  else
    RUN_FLAGS="$RUN_FLAGS --user $(id -u):$(id -g) --volume ${ROOT_DIR}:/workspace --workdir /workspace"
  fi

  mkdir -p "$ROOT_DIR/build"
  rm -f -- "$MANIFEST"

  FORWARD_CLEAN="--clean"
  if [ "$CLEAN" -eq 0 ]; then
    FORWARD_CLEAN="--no-clean"
  fi
  FORWARD_FLAT=""
  if [ "$FLAT" -eq 1 ]; then
    FORWARD_FLAT="--flat"
  fi
  FORWARD_PARALLEL="--parallel"
  if [ "$PARALLEL_BUILDS" -eq 0 ]; then
    FORWARD_PARALLEL="--serial"
  fi
  FORWARD_JOBS=""
  if [ -n "$CLI_JOBS" ]; then
    FORWARD_JOBS="--jobs $CLI_JOBS"
  fi

  FORWARD_ARCHES=""
  for arch in $TARGET_ARCHES; do
    FORWARD_ARCHES="$FORWARD_ARCHES --arch $arch"
  done

  if [ "$PARALLEL_BUILDS" -eq 1 ] && [ "$ARCH_COUNT" -gt 1 ]; then
    echo "=== Building native backend targets simultaneously inside container ($CONTAINER_ENGINE) ==="
  else
    echo "=== Running native backend build inside container ($CONTAINER_ENGINE) ==="
  fi

  # shellcheck disable=SC2086
  "$CONTAINER_ENGINE" run $RUN_FLAGS "$IMAGE_NAME" \
    scripts/build_native_backends_all.sh --worker \
      --build-type "$BUILD_TYPE" \
      $FORWARD_PARALLEL \
      $FORWARD_JOBS \
      $FORWARD_CLEAN \
      $FORWARD_FLAT \
      $FORWARD_ARCHES

else
  # Host build / container worker mode
  mkdir -p "$ROOT_DIR/build"
  if [ "$WORKER" -eq 0 ]; then
    rm -f -- "$MANIFEST"
  fi

  if [ "$PARALLEL_BUILDS" -eq 1 ] && [ "$ARCH_COUNT" -gt 1 ]; then
    echo "=== Building $ARCH_COUNT native backend targets simultaneously ($TARGET_ARCHES) ==="
    echo "=== Allocated $JOBS_PER_ARCH compiler jobs per architecture ==="

    STATUS_DIR=$(mktemp -d "$ROOT_DIR/build/status.XXXXXX")
    PIDS=""
    START_TIME=$(date +%s)

    for arch in $TARGET_ARCHES; do
      log_file="$ROOT_DIR/build/build-${arch}.log"
      status_file="$STATUS_DIR/status-${arch}"
      rm -f -- "$log_file" "$status_file"

      (
        compile_architecture "$arch" "$JOBS_PER_ARCH" "$CLEAN" "$FLAT" "$BUILD_TYPE" > "$log_file" 2>&1
        echo $? > "$status_file"
      ) &
      pid=$!
      PIDS="$PIDS $pid"
    done

    IS_TTY=0
    if [ -t 1 ]; then
      IS_TTY=1
    fi

    LINES_PRINTED=0
    COMPLETED_ARCHES=""
    FAILED_ARCHES=""
    TICK=0

    while :; do
      ALL_DONE=1
      OUTPUT_BUFFER=""

      for arch in $TARGET_ARCHES; do
        status_file="$STATUS_DIR/status-${arch}"
        if [ -f "$status_file" ]; then
          exit_code=$(cat "$status_file" 2>/dev/null || echo 1)
          if [ "$exit_code" -eq 0 ]; then
            case " $COMPLETED_ARCHES " in
              *" $arch "*) ;;
              *)
                COMPLETED_ARCHES="$COMPLETED_ARCHES $arch"
                if [ "$IS_TTY" -eq 0 ]; then
                  NOW=$(date +%s)
                  ELAPSED=$((NOW - START_TIME))
                  printf '  [%-7s] ✓ Succeeded in %ds\n' "$arch" "$ELAPSED"
                fi
                ;;
            esac
            line=$(printf '  [%-7s] %s ✓ Succeeded' "$arch" "$(render_progress_bar 100)")
          else
            case " $FAILED_ARCHES " in
              *" $arch "*) ;;
              *)
                FAILED_ARCHES="$FAILED_ARCHES $arch"
                if [ "$IS_TTY" -eq 0 ]; then
                  printf '  [%-7s] ✗ FAILED (exit code %s)\n' "$arch" "$exit_code"
                fi
                ;;
            esac
            line=$(printf '  [%-7s] ✗ FAILED (exit code %s)' "$arch" "$exit_code")
          fi
        else
          ALL_DONE=0
          status_text=$(get_build_status "$arch")
          line=$(printf '  [%-7s] %s' "$arch" "$status_text")
        fi

        if [ -z "$OUTPUT_BUFFER" ]; then
          OUTPUT_BUFFER="$line"
        else
          OUTPUT_BUFFER="$OUTPUT_BUFFER
$line"
        fi
      done

      if [ "$IS_TTY" -eq 1 ]; then
        if [ "$LINES_PRINTED" -gt 0 ]; then
          # Move cursor up and clear
          printf '\033[%dA' "$ARCH_COUNT"
        fi
        echo "$OUTPUT_BUFFER"
        LINES_PRINTED=$ARCH_COUNT
      else
        # In non-interactive terminal (e.g. CI), print periodic progress summary every 10 seconds
        TICK=$((TICK + 1))
        if [ "$ALL_DONE" -eq 0 ] && [ $((TICK % 10)) -eq 0 ]; then
          SUMMARY_LINE=""
          for arch in $TARGET_ARCHES; do
            st=$(get_build_status "$arch")
            SUMMARY_LINE="${SUMMARY_LINE}[$arch: $st] "
          done
          printf 'Progress: %s\n' "$SUMMARY_LINE"
        fi
      fi

      if [ "$ALL_DONE" -eq 1 ]; then
        break
      fi
      sleep 1
    done

    # Reap child processes
    for pid in $PIDS; do
      wait "$pid" 2>/dev/null || true
    done

    # Check for any failures
    TOTAL_ERRORS=0
    for arch in $TARGET_ARCHES; do
      status_file="$STATUS_DIR/status-${arch}"
      exit_code=1
      if [ -f "$status_file" ]; then
        exit_code=$(cat "$status_file" 2>/dev/null || echo 1)
      fi
      if [ "$exit_code" -ne 0 ]; then
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        echo "" >&2
        echo "======================================================================" >&2
        echo "ERROR: Native backend build for '$arch' failed (exit code $exit_code)" >&2
        echo "Last 40 lines of build/build-${arch}.log:" >&2
        echo "----------------------------------------------------------------------" >&2
        tail -n 40 "$ROOT_DIR/build/build-${arch}.log" >&2 || true
        echo "======================================================================" >&2
      fi
    done

    rm -rf -- "$STATUS_DIR"

    if [ "$TOTAL_ERRORS" -gt 0 ]; then
      echo "Error: One or more native backend builds failed." >&2
      exit 1
    fi

  else
    # Sequential build mode
    for native_arch in $TARGET_ARCHES; do
      echo "=== Building native Sendspin backend for $native_arch ==="
      compile_architecture "$native_arch" "$JOBS_PER_ARCH" "$CLEAN" "$FLAT" "$BUILD_TYPE"
    done
  fi
fi

if [ "$WORKER" -eq 0 ]; then
  MATRIX_COMPLETE=1
  for required_arch in x86_64 i386 aarch64 armv7; do
    case " $TARGET_ARCHES " in
      *" $required_arch "*) ;;
      *) MATRIX_COMPLETE=0 ;;
    esac
  done

  if [ "$MATRIX_COMPLETE" -eq 1 ]; then
    if [ "$RECORD_MATRIX" -eq 1 ]; then
      if command -v python3 >/dev/null 2>&1; then
        python3 "$ROOT_DIR/scripts/release.py" --record-native-backend-matrix
        echo "=== Native backend matrix complete ==="
      else
        echo "Warning: python3 not found; native release matrix could not be recorded" >&2
      fi
    else
      echo "=== Native backend builds complete; matrix recording disabled ==="
    fi
  else
    echo "=== Selected native backend builds complete; release matrix not recorded ==="
  fi

  for native_arch in $TARGET_ARCHES; do
    if [ "$FLAT" -eq 1 ]; then
      printf '%s: %s\n' "$native_arch" \
        "$ROOT_DIR/service.sendspin/resources/bin/sendspin-kodi-backend"
    else
      printf '%s: %s\n' "$native_arch" \
        "$ROOT_DIR/service.sendspin/resources/bin/$native_arch/sendspin-kodi-backend"
    fi
  done
fi
