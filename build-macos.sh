#!/bin/zsh
set -eu
cd -- "${0:A:h}"

python_executable=${VOLCASTUDIO_BUILD_PYTHON:-.venv/bin/python}
architecture=${VOLCASTUDIO_BUILD_ARCH:-$(uname -m)}
version=$("$python_executable" -c 'from app import __version__; print(__version__)')
args=()
if [[ -n ${VOLCASTUDIO_CODESIGN_IDENTITY:-} ]]; then
    args+=(--codesign-identity "$VOLCASTUDIO_CODESIGN_IDENTITY")
fi

"$python_executable" -m PyInstaller --noconfirm --clean --onedir --windowed \
    --name VolcaStudio --osx-bundle-identifier com.ondamondah.volcastudio \
    --target-architecture "$architecture" \
    --hidden-import mido.backends.rtmidi \
    --hidden-import app.device.sample_restore \
    --add-data 'app/device/init_pattern.vlcsplpattb:app/device' \
    "${args[@]}" app/main.py
/usr/bin/ditto -c -k --keepParent dist/VolcaStudio.app "dist/VolcaStudio-${version}-macOS-${architecture}-app.zip"
