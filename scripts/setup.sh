#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/voice-agent"
MODELS="$DATA/models"
BIN="$DATA/bin"
PIPER="$DATA/piper"
LOCAL_BIN="$HOME/.local/bin"
WHISPER_MODEL="${WHISPER_MODEL:-base.en}"
WHISPER_TAG="${WHISPER_TAG:-v1.8.1}"
PIPER_VERSION="${PIPER_VERSION:-2023.11.14-2}"
PIPER_VOICE="${PIPER_VOICE:-en_US-lessac-medium}"

mkdir -p "$MODELS" "$BIN" "$PIPER" "$LOCAL_BIN"

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

download() {
    local url="$1"
    local dest="$2"
    if [ -s "$dest" ]; then
        echo "exists: $dest"
        return
    fi
    echo "download: $url"
    curl -fL --retry 3 --connect-timeout 20 -o "$dest.part" "$url"
    mv "$dest.part" "$dest"
}

need curl
need python3

if [ ! -x "$BIN/whisper-cli" ]; then
    need git
    need cmake
    need g++
    need make
    echo "Building whisper.cpp ($WHISPER_TAG)..."
    rm -rf "$DATA/whisper.cpp"
    git clone --depth 1 --branch "$WHISPER_TAG" https://github.com/ggerganov/whisper.cpp.git "$DATA/whisper.cpp"
    cmake -S "$DATA/whisper.cpp" -B "$DATA/whisper.cpp/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_SERVER=OFF \
        -DGGML_NATIVE=ON
    cmake --build "$DATA/whisper.cpp/build" -j"$(nproc)" --target whisper-cli
    cp "$DATA/whisper.cpp/build/bin/whisper-cli" "$BIN/whisper-cli"
    chmod +x "$BIN/whisper-cli"
else
    echo "exists: $BIN/whisper-cli"
fi

download \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-${WHISPER_MODEL}.bin" \
    "$MODELS/ggml-${WHISPER_MODEL}.bin"

if [ ! -x "$PIPER/piper" ]; then
    echo "Installing Piper..."
    download \
        "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_x86_64.tar.gz" \
        "$DATA/piper.tar.gz"
    tar -xzf "$DATA/piper.tar.gz" -C "$DATA"
    chmod +x "$PIPER/piper"
else
    echo "exists: $PIPER/piper"
fi

download \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/${PIPER_VOICE}.onnx" \
    "$MODELS/${PIPER_VOICE}.onnx"
download \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/${PIPER_VOICE}.onnx.json" \
    "$MODELS/${PIPER_VOICE}.onnx.json"

ln -sf "$ROOT/bin/voice-agent" "$LOCAL_BIN/voice-agent"
chmod +x "$ROOT/bin/voice-agent"

PYTHONPATH="$ROOT/src" python3 -m voice_agent init

if [ "$WHISPER_MODEL" != "base.en" ]; then
    echo "Set paths.whisper_model in the config if you want to use ggml-${WHISPER_MODEL}.bin."
fi

echo
echo "Install complete."
echo "Launcher: $LOCAL_BIN/voice-agent"
echo "Run:      $LOCAL_BIN/voice-agent doctor"
echo "PTT:      $LOCAL_BIN/voice-agent listen --notify"
echo "KDE:      System Settings > Keyboard > Shortcuts > Custom Shortcuts > Command/URL"
echo "          Use the PTT command above and bind Meta+Space."
