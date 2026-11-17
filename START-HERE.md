# VolcaStudio 1.4.11 · macOS beta

VolcaStudio is an independent Mac app for the **Korg Volca Sample 2**. This beta focuses on installation, USB connection, and backup.

**Verified setup** — Intel Mac with macOS 13.7.8 and Python 3.11. Apple Silicon and other macOS versions are welcome as test setups. Compatibility on them is still being tested.

## Start the app

1. Install [Python 3.11 for macOS](https://www.python.org/downloads/macos/) if you do not already have Python 3.11. If the dependency installer reports a compiler error, install the Xcode Command Line Tools with `xcode-select --install` and try again.
2. Extract the ZIP to a writable folder. Double-click `VolcaStudio.command`. The first launch needs an internet connection to install Python dependencies. Leave the Terminal window open while the app is running.
3. If you prefer Terminal, open it in the extracted folder and run `./VolcaStudio.command`.

## First test

1. Power on your Volca Sample 2 and connect it to the Mac with a USB data cable.
2. In **Device**, click **Read slots**. Confirm that the occupied and free counts look right.
3. Choose **Backups → Save device snapshot…** and save the ZIP on your Mac. A snapshot contains the occupied sample slots and stored patterns.
4. Choose **Backups → Preview restore…**, open the ZIP, and check that its contents are shown. Close the preview. This test stops at preview.

If macOS sees the device but Volca does not respond, turn Volca off for five seconds, turn it back on, and use **Retry connection** if shown.

## What to report

- Mac model or chip and macOS version.
- Furthest step completed. If something failed, include the step and the exact error text.
- Whether you connected directly or through a hub.

Please share the error text or a screenshot with personal paths cropped out. Keep your snapshot ZIP on your Mac.
